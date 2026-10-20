from __future__ import annotations

from unittest.mock import Mock

import pytest

from management_portal import isa_client, resident_handoff
from management_portal.portal_ui_state import PortalUIStateStore


class _ResidentDomain:
    pass


class _SocietyService:
    def __init__(self) -> None:
        self.resident = _ResidentDomain()


class _Isa:
    """ISA calls the invocation code makes, answered from fixtures."""

    def __init__(self, settings: dict | None = None, responses: list | None = None) -> None:
        self.calls: list[tuple] = []
        self.settings = dict(settings or {})
        self.responses = list(responses or [])

    def __call__(self, actor, name, arguments=None, *, key="", version=None, **_options):
        self.calls.append((actor, name, dict(arguments or {}), key, version))
        if self.responses:
            return self.responses.pop(0)
        if name == "GetResidentSettings":
            return {"settings": dict(self.settings)}
        return {}

    def names(self) -> list[str]:
        return [call[1] for call in self.calls]


@pytest.fixture(autouse=True)
def isa(monkeypatch):
    fake = _Isa()
    monkeypatch.setattr(isa_client, "call", fake)
    return fake


@pytest.mark.parametrize("explicit_binding", [False, True])
@pytest.mark.parametrize("denied", [False, True])
def test_bound_invocation_uses_target_authorization_without_project_membership(
    monkeypatch, tmp_path, isa, explicit_binding, denied,
):
    """已绑定的后台调用按目标 Resident 的授权检查，不查询调用者的 Project 成员身份"""
    actor = "resident_333333333333"
    identity = {
        "resident_id": "resident_444444444444",
        "lineage_id": "lineage_333333333333",
    }
    store = PortalUIStateStore()
    if not explicit_binding:
        store.put(actor, "resident-invocations", "background-model", identity)
    # Timer-triggered Residents have no human Project memberships. Authorization
    # for their existing target must still be enforced by the Resident domain.
    access = Mock()
    access.project_catalog.return_value = [{"id": "RobotCompany"}]
    service = _SocietyService()
    service.resident.get_resident = Mock(return_value=identity)
    if denied:
        service.resident.get_resident.side_effect = PermissionError("target denied")
    monkeypatch.setattr(resident_handoff, "get_portal_access_store", lambda: access)
    monkeypatch.setattr(resident_handoff, "get_portal_ui_state_store", lambda: store)
    monkeypatch.setattr(resident_handoff, "get_society_service", lambda: service)
    options = dict(actor=actor, resident_scope="background-model")
    if explicit_binding:
        options["resident_binding"] = identity
    if denied:
        with pytest.raises(PermissionError, match="target denied"):
            resident_handoff.ensure_invocation_resident(**options)
    else:
        result = resident_handoff.ensure_invocation_resident(**options)
        assert result["resident_id"] == identity["resident_id"]
        assert result["lineage_id"] == identity["lineage_id"]
        if explicit_binding:
            assert not hasattr(service.resident, "update_resident")
    service.resident.get_resident.assert_called_once_with(identity["resident_id"], actor)
    access.project_catalog.assert_not_called()
    assert not isa.calls


def test_explicit_binding_uses_resident_configuration_not_caller_backend(monkeypatch, isa):
    """显式绑定的调用沿用 Resident 自身配置，忽略调用方给的后端、模型和目录"""
    service = _SocietyService()
    identity = {"resident_id": "resident_444444444444", "lineage_id": "lineage_333333333333"}
    service.resident.get_resident = Mock(return_value={**identity, "backend": "provider_api"})
    service.resident.update_resident = Mock()
    monkeypatch.setattr(resident_handoff, "get_society_service", lambda: service)

    assert resident_handoff.ensure_invocation_resident(
        actor="alice", resident_scope="review", resident_binding=identity,
        backend="codex", backend_route_id="zero", runtime_settings={"model": "wrong-model"},
        cwd="/wrong-directory",
    ) == identity
    service.resident.update_resident.assert_not_called()
    assert not isa.calls


@pytest.mark.parametrize("already_bound", [False, True])
def test_a_bound_local_resident_gets_the_business_working_directory_bound(monkeypatch, tmp_path, already_bound):
    """已绑定的 Resident 缺少业务工作目录时用 BindWorkspace 挂载，已挂载则不重复"""
    # A WALL task runs in its project root; the per-project role Resident must have it as a Workspace.
    service = _SocietyService()
    identity = {"resident_id": "resident_444444444444", "lineage_id": "lineage_333333333333"}
    service.resident.get_resident = Mock(return_value={**identity, "backend": "codex"})
    monkeypatch.setattr(resident_handoff, "get_society_service", lambda: service)
    workspaces = [{"path": str(tmp_path), "is_owned": False}] if already_bound else []
    fake = _Isa(responses=[{"resident": {"workspaces": workspaces}}, {"binding": {"path": str(tmp_path)}}])
    monkeypatch.setattr(isa_client, "call", fake)

    assert resident_handoff.ensure_invocation_resident(
        actor="alice", resident_scope="wall", resident_binding=identity, cwd=str(tmp_path),
    ) == identity
    expected = ["GetResident"] if already_bound else ["GetResident", "BindWorkspace"]
    assert fake.names() == expected
    if not already_bound:
        assert fake.calls[1][2] == {"resident_id": identity["resident_id"], "path": str(tmp_path.resolve())}


def test_invocation_identity_uses_declared_backend_when_projection_omits_it():
    """Resident 投影缺少 backend 时调用身份使用声明的后端"""
    identity = resident_handoff._invocation_identity({'resident_id': 'resident_123456789abc', 'lineage_id': 'lineage_123456789abc'}, resident_scope='resident-routing:alice', lineage_scope='resident-routing', backend='codex', created=False)
    assert identity['backend'] == 'codex'

def test_invocation_retries_update_while_lifecycle_finalizes(monkeypatch):
    """Resident 生命周期操作尚未完成时，调用重试挂载工作目录直到成功"""
    service = _SocietyService()
    bind_workspace = Mock(side_effect=[
        ValueError("Resident 生命周期操作正在完成，请稍后编辑"),
        {
            "resident_id": "resident_123456789abc",
            "lineage_id": "lineage_123456789abc",
            "title": "Daily Worker",
            "workspace_paths": ["/target"],
        },
    ])
    monkeypatch.setattr(resident_handoff, "_bind_working_directory", bind_workspace)
    monkeypatch.setattr(resident_handoff.time, "sleep", Mock())
    record = {
        "resident_id": "resident_123456789abc",
        "lineage_id": "lineage_123456789abc",
        "title": "Daily Worker",
        "backend": "codex",
    }

    configured = resident_handoff._configure_invocation_resident(
        service,
        record,
        "alice",
        backend="codex",
        cwd="/target",
    )

    assert configured == record
    assert bind_workspace.call_count == 2
    bind_workspace.assert_called_with("alice", record["resident_id"], "/target")


@pytest.mark.parametrize("failure", [PermissionError, KeyError])
@pytest.mark.parametrize("existing_resident", [False, True])
def test_invocation_preserves_unavailable_binding_without_replacement(
    monkeypatch,
    tmp_path,
    failure,
    existing_resident,
) -> None:
    """绑定的 Resident 或 Lineage 不可用时调用失败，保留原关联且不创建替代 Resident"""
    store = PortalUIStateStore()
    if existing_resident:
        store.put("alice", "resident-invocations", "scope", {
            "resident_id": "resident_111111111111",
            "lineage_id": "lineage_111111111111",
        })
    store.put("alice", "resident-invocation-lineages", "lineage-scope", {
        "lineage_id": "lineage_111111111111",
    })

    domain = Mock()
    domain.get_resident.side_effect = failure("unavailable binding")
    lineage = Mock()
    lineage.create_lineage_resident.side_effect = failure("unavailable binding")
    service = type("Service", (), {"resident": domain, "lineage": lineage})()
    monkeypatch.setattr(resident_handoff, "get_portal_ui_state_store", lambda: store)
    monkeypatch.setattr(resident_handoff, "get_society_service", lambda: service)

    for attempt in range(2):
        with pytest.raises(failure, match="unavailable binding"):
            resident_handoff.ensure_invocation_resident(
                actor="alice", resident_scope="scope", lineage_scope="lineage-scope",
            )
    domain.register_resident.assert_not_called()
    if existing_resident:
        lineage.create_lineage_resident.assert_not_called()
        assert store.get("alice", "resident-invocations", "scope")["value"]["resident_id"] == "resident_111111111111"
    assert store.get(
        "alice",
        "resident-invocation-lineages",
        "lineage-scope",
    )["value"]["lineage_id"] == "lineage_111111111111"


@pytest.mark.parametrize("actor", ["alice", "portal"])
def test_unbound_invocation_does_not_create_lineage(monkeypatch, tmp_path, actor):
    """未绑定的后台调用不能自动创建 Lineage

    普通用户与后台 actor 在缺少权威绑定时重复调用均失败，且不创建 Resident 或 Lineage、不写入替代关联。

    :importance: P1
    """
    store = PortalUIStateStore()
    domain = Mock()
    monkeypatch.setattr(resident_handoff, "get_portal_ui_state_store", lambda: store)
    monkeypatch.setattr(resident_handoff, "get_society_service", lambda: type("Service", (), {"resident": domain})())
    for attempt in range(2):
        with pytest.raises(ValueError, match="不会自动新建团队"):
            resident_handoff.ensure_invocation_resident(actor=actor, resident_scope="model", lineage_scope="background")
    domain.register_resident.assert_not_called()
    assert not store.get(actor, "resident-invocations", "model")
    assert not store.get(actor, "resident-invocation-lineages", "background")


@pytest.mark.parametrize("attachment_denied", [False, True])
def test_resident_invocation_uses_callers_registered_lineage(
    monkeypatch, tmp_path, attachment_denied,
) -> None:
    """Resident 发起的调用在调用者自己的 Lineage 中创建成员，并更新 Lineage 关联"""
    actor = "resident_333333333333"
    lineage_id = "lineage_333333333333"
    store = PortalUIStateStore()
    store.put(actor, "resident-invocation-lineages", "news", {
        "lineage_id": "lineage_111111111111",
    })
    domain = Mock()
    domain.get_resident.side_effect = lambda rid, actual_actor: {
        "resident_id": rid, "lineage_id": lineage_id, "backend": "codex",
    }
    lineage = Mock()

    def create(lineage_id_arg, payload, actual_actor):
        if attachment_denied:
            raise PermissionError("attachment denied")
        return {"resident": {"resident_id": "resident_444444444444", "lineage_id": lineage_id_arg}}

    lineage.create_lineage_resident.side_effect = create
    service = type("Service", (), {"resident": domain, "lineage": lineage})()
    monkeypatch.setattr(resident_handoff, "get_portal_ui_state_store", lambda: store)
    monkeypatch.setattr(resident_handoff, "get_society_service", lambda: service)

    def ensure():
        return resident_handoff.ensure_invocation_resident(
            actor=actor, resident_scope="daily-news", lineage_scope="news",
             backend_route_id="two",
        )

    if attachment_denied:
        with pytest.raises(PermissionError, match="attachment denied"):
            ensure()
    else:
        identity = ensure()
        assert identity["lineage_id"] == lineage_id
        assert identity["resident_id"] == "resident_444444444444"
        assert store.get(actor, "resident-invocation-lineages", "news")["value"]["lineage_id"] == lineage_id

    assert domain.get_resident.call_args_list[0].args == (actor, actor)
    called_lineage, payload, called_actor = lineage.create_lineage_resident.call_args.args
    assert called_actor == actor
    assert called_lineage == lineage_id
    assert payload["backend_route_id"] == "two"
    assert payload["creation_key"].startswith("invocation-create:")
    domain.register_resident.assert_not_called()


@pytest.mark.parametrize("binding_type", ["association", "explicit", "stale-explicit"])
def test_invocation_uses_current_membership_without_replacement(monkeypatch, tmp_path, binding_type):
    """迁移后复用 Resident 当前 Lineage，拒绝失效显式绑定

    复用关联以正式成员身份更新当前 Lineage；显式旧绑定失败，不创建或附加替代成员。

    :importance: P1
    """
    store = PortalUIStateStore()
    old_lineage = "lineage_111111111111"
    new_lineage = "lineage_222222222222"
    resident_id = "resident_111111111111"
    store.put("alice", "resident-invocations", "model", {"resident_id": resident_id, "lineage_id": old_lineage})
    store.put("alice", "resident-invocation-lineages", "background", {"lineage_id": old_lineage})
    domain = Mock()
    domain.get_resident.return_value = {"resident_id": resident_id, "lineage_id": new_lineage}
    monkeypatch.setattr(resident_handoff, "get_portal_ui_state_store", lambda: store)
    monkeypatch.setattr(resident_handoff, "get_society_service", lambda: type("Service", (), {"resident": domain})())
    options = {"actor": "alice", "resident_scope": "model", "lineage_scope": "background"}
    if binding_type != "association":
        options["resident_binding"] = {"resident_id": resident_id, "lineage_id": old_lineage if binding_type == "stale-explicit" else new_lineage}
    if binding_type == "stale-explicit":
        with pytest.raises(ValueError, match="与业务绑定不一致"):
            resident_handoff.ensure_invocation_resident(**options)
    else:
        for attempt in range(2):
            identity = resident_handoff.ensure_invocation_resident(**options)
            assert identity["resident_id"] == resident_id
            assert identity["lineage_id"] == new_lineage
        if binding_type == "association":
            assert store.get("alice", "resident-invocations", "model")["value"]["lineage_id"] == new_lineage
            assert store.get("alice", "resident-invocation-lineages", "background")["value"]["lineage_id"] == new_lineage
    domain.register_resident.assert_not_called()
