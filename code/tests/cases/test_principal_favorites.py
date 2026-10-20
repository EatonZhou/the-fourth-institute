"""
:module: lineage.team
:aspects: behavior, architecture-conformance
"""
from management_portal import serve_management_portal as portal
import pytest
from types import SimpleNamespace
from management_portal import principal_favorites
from management_portal.resources.errors import ResourceOperationError, ResourceServiceUnavailable


def test_project_favorites_persist_separately_from_lineage_and_do_not_grant_access():
    """验证 Project 收藏仅保存导航偏好，并正确显示权限。

    :importance: P1
    :design: decision_3e07c6d35cac, decision_8fe87d812a4c
    """
    project = "project:AI员工"
    lineage = "lineage:lineage_112233445566"
    assert portal._set_lineage_favorite("CASE-A", project, True)["favorites"] == [project]
    portal._set_lineage_favorite("CASE-A", lineage, True)
    assert portal._lineage_favorite_payload("CASE-A")["favorites"] == [project, lineage]
    assert portal._lineage_favorite_payload("other")["favorites"] == []
    # Removing a bookmark requires only its address, including after access loss.
    assert portal._set_lineage_favorite("CASE-A", project, False)["favorites"] == [lineage]
    with pytest.raises(ValueError):
        portal._set_lineage_favorite("CASE-A", "project:", True)
    with pytest.raises(ValueError):
        portal._set_lineage_favorite("CASE-A", "project:unsafe\nname", True)


def test_favorite_status_uses_authorized_tombstones_and_distinguishes_access_from_existence(monkeypatch):
    """收藏状态区分无权访问、资源缺失和服务不可用

    :module: platform.core
    :aspects: functional
    """
    calls = []
    def resolve(resource_id, *, actor):
        calls.append((resource_id, actor))
        if resource_id == "deleted":
            return {"record": {"title": "Former Lineage", "attributes": {"status": "deleted"}}}
        if resource_id == "denied":
            raise PermissionError("private title must not escape")
        if resource_id == "hidden":
            raise KeyError(resource_id)
        if resource_id == "missing":
            raise ResourceOperationError("resource_not_found", "missing", 404)
        if resource_id == "outage":
            raise ResourceServiceUnavailable("offline")
        return {"record": {"title": "Visible", "attributes": {"leader_resident_id": "resident_112233445566"}}}
    from management_portal import permission_system
    monkeypatch.setattr(permission_system, 'readable_ids',
                        lambda instruction, parameter, ids, **kw: set(ids) - {'hidden'})
    monkeypatch.setattr(principal_favorites, 'get_resource_service', lambda: SimpleNamespace(resolve=resolve))
    addresses = ["lineage:deleted", "lineage:denied", "lineage:hidden", "lineage:missing", "project:outage", "project:visible"]
    forgotten = []
    payload = principal_favorites.project_favorite_statuses("CASE-A", {"favorites": addresses}, forgotten.append)
    statuses = payload["favorite_statuses"]
    # A deleted Lineage or Project leaves My Favorite and the stored bookmarks.
    assert "lineage:deleted" not in statuses and forgotten == ["lineage:deleted"]
    assert statuses["lineage:denied"] == {"status": "denied"}
    assert statuses["lineage:hidden"] == {"status": "denied"}
    assert statuses["lineage:missing"] == {"status": "missing"}
    assert statuses["project:outage"] == {"status": "unavailable"}
    assert statuses["project:visible"]["status"] == "available"
    assert payload["favorites"] == addresses[1:]
    assert calls == [(address.split(":")[1], "CASE-A") for address in addresses if address != "lineage:hidden"]


def test_a_deleted_lineage_leaves_my_favorite(monkeypatch):
    """已删除的 Lineage 自动移出收藏

    :design: decision_3e07c6d35cac, decision_8fe87d812a4c"""
    deleted, kept = "lineage:lineage_aabbccddeeff", "project:Kept"
    portal._set_lineage_favorite("CASE-A", deleted, True)
    portal._set_lineage_favorite("CASE-A", kept, True)

    def resolve(resource_id, *, actor):
        status = "deleted" if resource_id == "lineage_aabbccddeeff" else "active"
        return {"record": {"title": resource_id, "status": status, "attributes": {}}}

    monkeypatch.setattr(principal_favorites, "get_resource_service", lambda: SimpleNamespace(resolve=resolve))
    payload = principal_favorites.project_favorite_statuses(
        "CASE-A", portal._lineage_favorite_payload("CASE-A"),
        lambda selection: portal._set_lineage_favorite("CASE-A", selection, False),
    )
    assert payload["favorites"] == [kept]
    assert portal._lineage_favorite_payload("CASE-A")["favorites"] == [kept]


def test_user_navigation_drops_a_deleted_favorite(monkeypatch):
    """用户导航剔除已删除的收藏

    :design: decision_3e07c6d35cac, decision_8fe87d812a4c"""
    from management_portal import presentation_store, web_isa

    portal._set_lineage_favorite("CASE-A", "lineage:lineage_aabbccddeeff", True)
    portal._set_lineage_favorite("CASE-A", "lineage:lineage_112233445566", True)

    def resolve(resource_id, *, actor):
        status = "deleted" if resource_id == "lineage_aabbccddeeff" else "active"
        return {"record": {"title": resource_id, "status": status, "attributes": {}}}

    monkeypatch.setattr(principal_favorites, "get_resource_service", lambda: SimpleNamespace(resolve=resolve))
    monkeypatch.setattr(web_isa, "web", lambda: portal)
    favorite_ids = presentation_store.favorite_ids
    monkeypatch.setattr(presentation_store, "favorite_ids", lambda caller, surface:
                        [] if surface == web_isa.RESIDENT_FAVORITE_SURFACE else favorite_ids(caller, surface))
    assert web_isa._navigation_items("CASE-A") == ["lineage_112233445566"]
    assert portal._lineage_favorite_payload("CASE-A")["favorites"] == ["lineage:lineage_112233445566"]


def test_favorites_batch_current_policy_and_keep_bookmarks_when_policy_unavailable(monkeypatch):
    """收藏一次批量向权限端口询问可读性，权限系统不可用时保留书签

    :module: platform.core
    :aspects: functional
    """
    from management_portal import permission_system
    calls = []
    def unavailable(instruction, parameter, identifiers, **kw):
        calls.append((instruction, parameter, list(identifiers)))
        raise RuntimeError('permission system offline')
    monkeypatch.setattr(permission_system, 'readable_ids', unavailable)
    monkeypatch.setattr(principal_favorites, 'get_resource_service', lambda: SimpleNamespace(
        resolve=lambda *a, **kw: pytest.fail('Unverified bookmark was resolved')))
    favorites = ['lineage:lineage_112233445566', 'project:AI员工']
    result = principal_favorites.project_favorite_statuses('CASE-A', {'favorites': favorites})
    assert result['favorites'] == favorites
    assert result['favorite_statuses'] == {key: {'status': 'unavailable'} for key in favorites}
    assert calls == [('ResolveResource', 'address', ['lineage_112233445566', 'AI员工'])]
