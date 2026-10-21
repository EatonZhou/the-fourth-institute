from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from management_portal.resources.providers import install_mutation_providers, test as test_provider
from management_portal.resources.service import ResourceService
from management_portal.test_framework.catalog import CatalogRepository
from management_portal.test_framework.models import CatalogError, TestCase as FrameworkTestCase


TEST_ID = "test_0123456789ab"


def _case(resource_id: str = TEST_ID) -> dict:
    return {
        "id": resource_id,
        "key": "resource.test-case",
        "title": "Test Resource",
        "description": "Verify Test resources.",
        "importance": "P1",
        "module": "resource.service",
        "aspects": ["functional"],
        "is_design_point": False,
        "design_refs": [],
        "lifecycle": "active",
        "implementations": [],
    }


def test_test_case_identity_accepts_only_canonical_resource_ids() -> None:
    """用例 ID 只接受 test_ 加 12 位字符的正式资源 ID"""
    assert FrameworkTestCase.parse(_case()).id == TEST_ID
    with pytest.raises(CatalogError, match="test_<12位"):
        FrameworkTestCase.parse(_case("case-old-identity"))


def test_test_provider_resolves_and_reads_catalog_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test 资源提供方可以解析并读取目录中的用例"""
    cases_root = tmp_path / "tests/cases"
    cases_root.mkdir(parents=True)
    (cases_root / "test_sample.py").write_text("def test_sample():\n    pass\n", encoding="utf-8")
    (cases_root / "case_ids.json").write_text(json.dumps({
        "schema": "tfi.test-case-ids.v1", "ids": {"tests/cases/test_sample.py::test_sample": TEST_ID}}))
    monkeypatch.setattr(test_provider, "catalog_repository", lambda: CatalogRepository(
        root=tmp_path, verify_sources=False))

    resolved = test_provider.resolve_test_address(TEST_ID)
    content, content_type, _metadata = test_provider.read_test_content(resolved)

    assert resolved["record"]["read_scope"] == "global"
    assert json.loads(content)["id"] == TEST_ID
    assert content_type == "application/json"


def test_test_resources_have_no_update() -> None:
    """测试资源只读，资源服务不提供修改入口

    标题、说明与分类只写在测试源码中；对 test 资源调用修改时，资源服务找不到修改提供方并拒绝。
    """
    index = mock.Mock()
    index.get_resource.return_value = {
        "resource_id": TEST_ID,
        "resource_type": "test",
        "attributes": {"read_scope": "global"},
    }
    index.matching_adapters.return_value = []
    index.change_targets.return_value = []
    service = ResourceService(index_provider=index, mutation_provider_loader=install_mutation_providers)

    with pytest.raises(ValueError, match="no mutation provider registered for test"):
        service.update(TEST_ID, {"lifecycle": "retired"}, actor="operator", resource_type="test")
