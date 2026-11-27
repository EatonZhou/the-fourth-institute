"""A named resource operation finds a resource the search index has not caught up with.

:importance: P1
"""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from management_portal import permission_system
from management_portal.resources.service import ResourceService

WORKBENCH = "workbench_0123456789ab"
ACTOR = "resident_0123456789ab"


def _service(monkeypatch, index) -> ResourceService:
    monkeypatch.setattr(permission_system, "allows", lambda *args, **kwargs: True)
    return ResourceService(index_provider=index, type_registry=Mock())


def test_a_named_operation_runs_on_a_resource_registered_moments_ago(monkeypatch):
    """具名资源指令作用于刚注册、索引尚未收录的资源

    UpdateWorkbenchPage 的目标在搜索索引中暂时找不到时（例如刚从页面创建的工作台，索引刷新还在进行），目标由资源 ID 本身确定，直接交给资源自己的变更提供方；设计师保存生成结果不会因此得到 instruction.not_found。
    """
    # A workbench created from its page is registered without waiting for the
    # index; the designer saves its generation right after (10-07 TAU).
    index = Mock()
    index.get.side_effect = KeyError(WORKBENCH)
    service = _service(monkeypatch, index)
    page = Mock(return_value={"revision": 2})
    service.register_mutation_provider("workbench", update_workbench_page=page)
    assert service.update_workbench_page(
        WORKBENCH, "job", 1, "<h1>Page</h1>", actor=ACTOR, title="Page", description="Done",
    ) == {"revision": 2}
    page.assert_called_once_with(WORKBENCH, "job", 1, "<h1>Page</h1>", actor=ACTOR,
                                 title="Page", description="Done")


def test_a_resource_of_another_type_is_not_accepted(monkeypatch):
    """具名指令只接受它声明的资源类型

    UpdateWorkbenchPage 不会因为 ID 格式合法就作用于其他类型的资源。
    """
    service = _service(monkeypatch, Mock())
    page = Mock()
    service.register_mutation_provider("workbench", update_workbench_page=page)
    with pytest.raises(ValueError, match="UpdateWorkbenchPage"):
        service.update_workbench_page("paper_0123456789ab", "job", 1, "<h1>Page</h1>", actor=ACTOR)
    page.assert_not_called()
