from __future__ import annotations

from tests.cases.backpack_http_test_support import backpack_request

import copy
import json
from types import SimpleNamespace

import pytest

from management_common.resource_identity import get_resource_type_registry
from management_portal.principal_backpack import PrincipalBackpack
from management_portal.resources.contracts import ResourceQuery


RESIDENT = "resident_0123456789ab"


class Access:
    records = {
        "admin": {"username": "admin", "status": "active", "category": "portal_admin"},
        "alice": {"username": "alice", "status": "active", "category": "member", "email": "private@example.test"},
        "bob": {"username": "bob", "status": "active", "category": "member"},
    }

    def user(self, principal):
        return copy.deepcopy(self.records[principal])

    def list_users(self, names=()):
        return copy.deepcopy(list(self.records.values()))

    def is_active(self, principal):
        return principal in self.records

    def is_portal_admin(self, principal):
        return principal == "admin"


class Resources:
    def __init__(self):
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        query = ResourceQuery.from_kwargs(kwargs)
        return {"resources": [{"resource_id": RESIDENT, "resource_type": "resident", "title": "Researcher", "attributes": {"status": "active", "secret": "never-render"}}], "total": 120, "has_more": query.offset + query.limit < 120}

    def get(self, identifier, *, actor):
        self.calls.append((identifier, actor))
        if identifier != RESIDENT:
            raise KeyError(identifier)
        return {"resource_id": RESIDENT, "resource_type": "resident", "title": "Researcher", "status": "active", "project_ids": ["P"], "relations": [{"kind": "workspace", "target_id": "workspace_0123456789ab"}], "attributes": {"secret": "never-render"}}


@pytest.fixture
def backpack(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_RESIDENT_AVATAR_ROOT", str(tmp_path / "avatars"))
    backend = PrincipalBackpack(access=Access(), resources=Resources())
    return backend


def test_backpack_catalog_preserves_authenticated_query_and_pagination(backpack):
    """背包名册保持登录身份并按请求分页

    搜索分页保持当前读取身份；普通用户不取得管理员列表，响应不包含内部字段。

    :module: security.identity-access
    :importance: P1
    :design: decision_5bc3fdfc7eec
    """
    result = backpack.catalog("alice", query="Research", offset=60, limit=20)
    assert result["users"] == []
    assert result["residents"][0]["level"] == "unassigned"
    assert result["has_more"]
    query = backpack.resources.calls[-1]
    assert query["actor"] == "alice" and query["q"] == "Research" and query["offset"] == 60
    assert "never-render" not in json.dumps(result)
    assert len(backpack.catalog("admin")["users"]) == 3


def test_backpack_identity_boundaries_and_current_holdings_are_explicit(backpack):
    """背包档案遵守身份边界并明确现有持有物

    普通 User 只读自身、管理员可读其他用户；Resident 以当前调用者身份读取，档案不附带凭证字段。

    :module: security.identity-access
    :importance: P1
    :design: decision_5bc3fdfc7eec
    """
    result = backpack.detail("alice", "alice")
    assert "credentials" not in result and "credential_state" not in result
    assert result["possessions"] == []
    assert "private@example.test" not in json.dumps(result)
    assert backpack.detail("alice", "bob")["principal"]["id"] == "bob"
    assert backpack.detail("admin", "bob")["principal"]["id"] == "bob"
    resident = backpack.detail("alice", RESIDENT)
    assert resident["principal"]["status"] == "active"
    assert resident["possessions"][1]["kind"] == "workspace"
    assert backpack.resources.calls[-1] == (RESIDENT, "alice")
    with pytest.raises(ValueError):
        backpack.detail("alice", "portal")
    with pytest.raises(PermissionError):
        backpack.catalog("portal")
    with pytest.raises(KeyError):
        backpack.detail("admin", "missing")


def test_package_catalog_and_admin_profiles_add_no_authority_summary(backpack, monkeypatch):
    """背包资源类型目录完整，管理员档案不附加权限摘要或凭证字段"""
    kinds = sorted(get_resource_type_registry().types)
    assert backpack.catalog("admin")["resource_types"] == kinds
    result = backpack.detail("admin", "admin")
    assert result["possessions"] == []
    assert "authority_summary" not in result and "credentials" not in result
    assert "authority_summary" not in backpack.detail("admin", "alice")
    assert "authority_summary" not in backpack.detail("admin", RESIDENT)


