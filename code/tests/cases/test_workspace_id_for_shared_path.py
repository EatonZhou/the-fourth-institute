"""Registration and every binding share the path identity constraint.

:module: spec.design-governance
"""
import pytest
from management_portal.resources.service import ResourceService


def test_repeated_registration_uses_one_canonical_workspace(tmp_path):
    """同一路径重复登记得到同一个 Workspace ID，指定其他 ID 时拒绝"""
    service=ResourceService.__new__(ResourceService)
    service.new_id=lambda _: 'workspace_aaaaaaaaaaaa'
    path=str(tmp_path/'shared')
    assert service.workspace_id_for_path(path)=='workspace_aaaaaaaaaaaa'
    assert service.workspace_id_for_path(path)=='workspace_aaaaaaaaaaaa'
    with pytest.raises(ValueError,match='different identity'):
        service.workspace_id_for_path(path,preferred_id='workspace_bbbbbbbbbbbb')


def test_a_new_path_accepts_a_preferred_identity(tmp_path):
    """新路径登记 Workspace 时采用调用方指定的 ID"""
    service=ResourceService.__new__(ResourceService)
    service.new_id=lambda _: 'workspace_aaaaaaaaaaaa'
    assert service.workspace_id_for_path(str(tmp_path/'new'),preferred_id='workspace_bbbbbbbbbbbb')=='workspace_bbbbbbbbbbbb'
