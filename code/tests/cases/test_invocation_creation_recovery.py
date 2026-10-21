"""Crash recovery of invocation Resident creation across the UI association store.

The creation request is frozen before it is sent and carries its request ID as
the creation key, so resending it after an unknown outcome returns the
Resident it already created.
"""
from types import SimpleNamespace

import pytest

from management_portal import resident_handoff as invocation
from management_portal.portal_ui_state import PortalUIStateStore


LINEAGE = "lineage_111111111111"


class Lineages:
    """A SocietyService that creates each creation key's Resident once."""

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}
        self.by_key: dict[str, str] = {}
        self.requests: list[dict] = []
        self.fail_after_create = False

    def create_lineage_resident(self, lineage_id, payload, actor):
        assert actor == "alice"
        self.requests.append(dict(payload))
        key = payload["creation_key"]
        if key not in self.by_key:
            resident_id = f"resident_{len(self.records) + 1:012d}"
            self.by_key[key] = resident_id
            self.records[resident_id] = {"resident_id": resident_id, "lineage_id": lineage_id, "backend": payload["backend"]}
            if self.fail_after_create:
                self.fail_after_create = False
                raise TimeoutError("committed response lost")
        return {"resident": dict(self.records[self.by_key[key]])}


def test_concurrent_discussion_creation_uses_one_resident(monkeypatch):
    """同一讨论的并发首次发送跨 Store 实例串行解析，只创建一个普通 Resident。

    同一讨论的并发首次发送跨 Store 实例串行解析，只创建一个普通 Resident。

    :module: resident.conversation
    """
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    lineages = Lineages()
    ready = Barrier(2)
    service = SimpleNamespace(lineage=SimpleNamespace(create_lineage_resident=lineages.create_lineage_resident,
                                                      get_lineage=lambda rid, actor: {"lineage_id": rid}),
                              resident=SimpleNamespace(get_resident=lambda rid, actor: lineages.records[rid]))
    monkeypatch.setattr(invocation, "get_society_service", lambda: service)
    monkeypatch.setattr(invocation, "get_portal_ui_state_store", lambda: PortalUIStateStore())
    monkeypatch.setattr(invocation, "_configure_invocation_resident", lambda service, record, actor, **options: record)

    def send(_):
        ready.wait(timeout=5)
        return invocation.ensure_invocation_resident(actor="alice", resident_scope="resource-chat:alice:repo_111111111111",
                                                     lineage_binding=LINEAGE, title="Discussion", backend_route_id="one")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send, range(2)))
    assert len(lineages.requests) == 1
    assert {result["resident_id"] for result in results} == {"resident_000000000001"}


RECOVERED = ("before_dispatch", "response_lost", "identity_write", "association_write", "configuration", "changed_parameters")
REFUSED = ("revoked", "deleted", "moved", "changed_backend", "changed_lineage")


@pytest.mark.parametrize("scenario", RECOVERED + REFUSED)
def test_invocation_creation_recovers_the_original_identity(monkeypatch, scenario):
    """创建讨论 Resident 中途失败后重发原请求取回同一个 Resident，已撤权、删除或参数冲突时拒绝"""
    scope = f"resource-chat:alice:{scenario}"
    lineages = Lineages()
    flags = {"armed": True, "revoked": False}
    store = PortalUIStateStore()

    def resident(rid, actor):
        if flags["revoked"]:
            raise PermissionError("access revoked")
        return lineages.records[rid]

    def configure(service, record, actor, **options):
        if scenario == "configuration" and flags["armed"]:
            flags["armed"] = False
            raise OSError("configuration failed")
        return record

    service = SimpleNamespace(
        lineage=SimpleNamespace(create_lineage_resident=lineages.create_lineage_resident,
                                get_lineage=lambda rid, actor: {"lineage_id": rid}),
        resident=SimpleNamespace(get_resident=resident),
    )
    monkeypatch.setattr(invocation, "get_society_service", lambda: service)
    monkeypatch.setattr(invocation, "get_portal_ui_state_store", lambda: store)
    monkeypatch.setattr(invocation, "_configure_invocation_resident", configure)
    original_put = store.put

    def put(actor, namespace, key, value):
        if flags["armed"] and (
            scenario == "before_dispatch"
            or scenario == "identity_write" and value.get("resident_id")
            or scenario in {"association_write", "deleted", "moved", "revoked"}
            and namespace == invocation.INVOCATION_ASSOCIATION_NAMESPACE
        ):
            flags["armed"] = False
            raise OSError("association storage unavailable")
        return original_put(actor, namespace, key, value)

    monkeypatch.setattr(store, "put", put)
    if scenario in {"response_lost", "changed_parameters", "changed_backend", "changed_lineage"}:
        lineages.fail_after_create = True
    options = dict(actor="alice", resident_scope=scope, lineage_binding=LINEAGE, title="Discussion", backend_route_id="one")
    with pytest.raises((OSError, TimeoutError)):
        invocation.ensure_invocation_resident(**options)
    assert len(lineages.records) == (0 if scenario == "before_dispatch" else 1)

    before = store.get("alice", invocation.INVOCATION_CREATION_NAMESPACE, scope)
    if scenario == "revoked":
        flags["revoked"] = True
    if scenario == "deleted":
        lineages.records.clear()
    if scenario == "moved":
        next(iter(lineages.records.values()))["lineage_id"] = "lineage_222222222222"
    if scenario == "changed_parameters":
        options.update(title="Changed title", backend_route_id="two", runtime_settings={"model": "changed"})
    if scenario == "changed_lineage":
        options["lineage_binding"] = "lineage_222222222222"
    if scenario == "changed_backend":
        options["backend"] = "provider_api"
    if scenario in REFUSED:
        for _ in range(2):
            with pytest.raises((PermissionError, KeyError, ValueError)):
                invocation.ensure_invocation_resident(**options)
        assert store.get("alice", invocation.INVOCATION_CREATION_NAMESPACE, scope) == before
        assert len(lineages.records) == (0 if scenario == "deleted" else 1)
        return
    result = invocation.ensure_invocation_resident(**options)
    assert result["resident_id"] == "resident_000000000001"
    assert len(lineages.records) == 1
    assert store.get("alice", invocation.INVOCATION_ASSOCIATION_NAMESPACE, scope)["value"]["resident_id"] == result["resident_id"]
    # Every resend is the original frozen request with its creation key.
    assert all(request == lineages.requests[0] for request in lineages.requests)
