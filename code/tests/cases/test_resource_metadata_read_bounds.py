"""Resource type and actor metadata reads stay bounded."""
from __future__ import annotations

import json
import os
import time

from management_common import resource_identity
from management_common.resource_identity import ResourceTypeRegistry
from management_portal.portal_access_control import get_portal_access_store



def _counted(monkeypatch, owner, name: str, *, static: bool = False) -> list[int]:
    calls = [0]
    original = getattr(owner, name)

    def counting(*args, **kwargs):
        calls[0] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, staticmethod(counting) if static else counting)
    return calls


def _write_registry(path, types) -> None:
    path.mkdir(exist_ok=True)
    (path / '_registry.json').write_text(json.dumps({
        "schema_version": 1, "type_pattern": "[a-z][a-z0-9]*", "code_pattern": "[0-9a-f]{12}",
        "id_exceptions": {},
    }))
    for name in types:
        (path / f'{name}.json').write_text(json.dumps({
            'type': name, 'category': 'resource', 'title': {'zh': name, 'en': name},
            'description': name, 'provider': '', 'catalog_section': '', 'catalog_order': 0,
            'registerable': False, 'scope': 'shared', 'unit': 'core', 'enabled_by_default': True,
        }))


def test_the_type_registry_file_is_checked_at_most_once_per_interval(tmp_path, monkeypatch):
    """资源类型注册表文件在间隔内最多检查一次"""
    path = tmp_path / "resource-types"
    _write_registry(path, ["device"])
    registry = ResourceTypeRegistry(path)
    checks = _counted(monkeypatch, registry, "_signature")
    for _ in range(100):
        assert registry.parse("device_0123456789ab").resource_type == "device"
    assert checks[0] == 1
    _write_registry(path, ["device", "article"])
    os.utime(path, ns=(time.time_ns() + 1_000_000_000,) * 2)
    monkeypatch.setattr(resource_identity.ResourceTypeRegistry, "RECHECK_SECONDS", 0.0)
    assert registry.is_registered("article")


def test_access_checks_copy_only_the_checked_user(monkeypatch):
    """访问检查只复制被检查的用户

    is_active 只读取并复制被检查用户的记录，不再每次复制全部 Portal 用户；结果与完整快照一致。
    """
    store = get_portal_access_store()
    expected = store.is_active("nobody")
    def full_copy():
        raise AssertionError("an access check copied every user")
    monkeypatch.setattr(store, "snapshot", full_copy)
    assert store.is_active("nobody") == expected
