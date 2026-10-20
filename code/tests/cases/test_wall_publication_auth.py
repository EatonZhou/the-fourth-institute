import pytest
from management_portal import http_wall, page_api_auth
from management_portal import serve_management_portal as portal


def test_wall_uses_authenticated_resident_and_rejects_anonymous_and_cross_origin(monkeypatch):
    """WALL 写接口使用已认证的 Resident 身份，拒绝匿名请求和跨源的用户请求"""
    request=object()
    auth={'type':'agent','id':'resident_403556d33f74'}
    monkeypatch.setattr(portal,'_get_agent_api_auth',lambda _:auth)
    seen=[]
    monkeypatch.setattr(page_api_auth,'authorize',lambda a:seen.append(a) or a['id'])
    assert http_wall.api_actor(portal,request,write=True)==auth['id']
    assert seen==[auth]
    monkeypatch.setattr(portal,'_get_agent_api_auth',lambda _:None)
    with pytest.raises(PermissionError):http_wall.api_actor(portal,request,write=True)
    monkeypatch.setattr(portal,'_get_agent_api_auth',lambda _:{'type':'human','id':'owner'})
    monkeypatch.setattr(portal,'_same_origin_request',lambda _:False)
    with pytest.raises(PermissionError):http_wall.api_actor(portal,request,write=True)
