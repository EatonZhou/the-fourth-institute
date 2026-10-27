from __future__ import annotations
from management_portal.society_service import (
    business_fact_views,
    resident_access,
    resident_conversation_projection_domain,
    resident_record_domain,
)
from management_portal.society_service import resident_catalog
from management_portal.society_service import resident_conversation_stream
from management_portal.society_service.domain_ports import resource_port

import threading

import pytest

from management_portal.society_service import lineage_domain
from management_portal.society_service import resident_domain as backend
from management_portal.society_service import resident_directory_domain
from management_portal.society_service import resident_runtime_domain
from management_portal.society_service import resident_workspace_domain
from management_portal.society_service import resident_lineage
from management_portal.society_service import resource_client
from management_portal.society_service.resident_domain_host import (
    ResidentDomainHost,
)
from tests.cases.database_test_support import register_owned_workspace


def _other_lineage_can_enter_state_lock() -> bool:
    acquired: list[bool] = []

    def probe() -> None:
        with lineage_domain._lineage_scope("lineage_eeeeeeeeeeee"):
            acquired.append(True)

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join(timeout=2)
    return acquired == [True]


def test_resident_workspace_roots_read_outside_global_lock(
    tmp_path,
    monkeypatch,
) -> None:
    """读取 Resident 的 Workspace 绑定时业务视图在全局锁外加载，其他 Lineage 可同时进入状态锁"""
    record = {
        "resident_id": "resident_0123456789ab",
        "owned_workspace_id": "workspace_0123456789ab",
        "owned_workspace": {"workspace_id": "workspace_0123456789ab", "path": str(tmp_path)},
        "workspace_path": str(tmp_path),
        "workspace_bindings": [],
        "registration_state": "ready",
    }
    monkeypatch.setattr(resident_record_domain, "find_resident", lambda _resident_id: record)
    monkeypatch.setattr(
        resident_access,
        "has_resident_workspace_authority",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(resident_directory_domain, "ensure_resident_visible_to_actor", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(resident_access, "can_manage", lambda *_args: True)
    monkeypatch.setattr(resident_lineage, "snapshot", lambda _record: {"residents": []})
    def canonical_views_stub(records, **_kwargs):
        assert _other_lineage_can_enter_state_lock()
        return list(records)

    monkeypatch.setattr(
        business_fact_views,
        'canonical_views',
        canonical_views_stub,
    )
    monkeypatch.setattr(
        resident_runtime_domain,
        "managed_derived_residents",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        resident_record_domain,
        "resident_workspace_roots_with_usage",
        lambda _record: (
            [{
                "workspace_id": "workspace_0123456789ab",
                "path": str(tmp_path),
                "is_primary": True,
            }],
            str(tmp_path),
        ),
    )

    payload = backend.get_resident_workspace_roots("resident_0123456789ab", "sample_admin")
    assert payload["roots"][0]["path"] == str(tmp_path) and payload["roots"][0]["is_dir"] is True


def test_resident_folder_opens_one_lineage_identity_snapshot(
    tmp_path,
    monkeypatch,
) -> None:
    """打开 Resident 文件夹时 Lineage 访问信息只读取一次"""
    record = {
        "resident_id": "resident_0123456789ab",
        "lineage_id": "lineage_0123456789ab",
        "workspace_id": "workspace_0123456789ab",
        "workspace_path": str(tmp_path),
        "workspace_bindings": [],
        "registration_state": "ready",
    }
    registry = {
        "residents": [
            {"resident_id": f"resident_{index:012x}"}
            for index in range(8)
        ]
    }
    reads = 0

    def lineage_identity(_lineage_id):
        nonlocal reads
        reads += 1
        return {
            "lineage_id": record["lineage_id"],
            "resident_ids": [record["resident_id"]],
            "leader_resident_id": record["resident_id"],
        }

    def ensure_visible(*_args, **_kwargs):
        for _ in range(3):
            resident_runtime_domain.lineage_identity_record(record)

    def actor_derived_residents(*_args, **_kwargs):
        for _resident in registry["residents"]:
            resident_runtime_domain.lineage_identity_record(record)
        return registry["residents"]

    monkeypatch.setattr(lineage_domain, "lineage_access_projection", lineage_identity)
    monkeypatch.setattr(resident_record_domain, "find_resident", lambda _resident_id: record)
    monkeypatch.setattr(
        resident_access,
        "has_resident_workspace_authority",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(resident_directory_domain, "ensure_resident_visible_to_actor", ensure_visible)
    monkeypatch.setattr(resident_workspace_domain, "ensure_resident_visible_to_actor", ensure_visible)
    monkeypatch.setattr(resident_lineage, "snapshot", lambda _record: registry)
    monkeypatch.setattr(
        business_fact_views,
        'canonical_views',
        lambda records, **_kwargs: [dict(item) for item in records],
    )
    monkeypatch.setattr(
        resident_runtime_domain,
        "managed_derived_residents",
        actor_derived_residents,
    )
    monkeypatch.setattr(
        resident_access,
        "can_manage",
        lambda *_args: bool(resident_runtime_domain.lineage_identity_record(record)),
    )
    monkeypatch.setattr(
        resident_record_domain,
        "resident_workspace_roots_with_usage",
        lambda _record: ([], ""),
    )

    payload = backend.get_resident_workspace_roots(record["resident_id"], "sample_admin")

    assert payload["resident_id"] == record["resident_id"]
    assert reads == 1


def test_context_pressure_query_does_not_rebuild_resident_runtime_projection(
    monkeypatch,
) -> None:
    """查询 Resident 上下文压力不读取对话记录"""
    resident_id = "resident_0123456789ab"
    record = {
        "resident_id": resident_id,
        "lineage_id": "lineage_0123456789ab",
        "session_state": "running",
    }
    monkeypatch.setattr(
        resident_lineage,
        "all",
        lambda: [record],
    )
    monkeypatch.setattr(
        lineage_domain,
        "lineage_access_projection",
        lambda _lineage_id: {
            "lineage_id": record["lineage_id"],
            "lineage_node_ids": [resident_id],
            "leader_resident_id": resident_id,
        },
    )
    class Catalog:
        def get_many(self, resident_ids):
            return [record] if resident_id in resident_ids else []

    monkeypatch.setattr(resident_catalog, "get_catalog", lambda: Catalog())
    monkeypatch.setattr(
        business_fact_views,
        'canonical_views',
        lambda records, **_kwargs: [dict(item) for item in records],
    )

    monkeypatch.setattr(
        resident_conversation_projection_domain,
        "resident_conversation_messages",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("context pressure must not read transcripts")
        ),
    )
    monkeypatch.setattr(
        resident_directory_domain,
        "_resident_scope_context_pressure",
        lambda *_args, **_kwargs: {"heat_level": "normal"},
    )
    projection = backend.get_resident_context_pressures(
        [resident_id],
        "sample_admin",
    )

    assert projection == {resident_id: {"heat_level": "normal"}}


def test_user_message_count_batch_uses_database_facts_without_transcript(
    monkeypatch,
) -> None:
    """批量统计 Resident 的用户消息数直接查数据库，不读取对话记录"""
    resident_id = "resident_0123456789ab"
    record = {
        "resident_id": resident_id,
    }
    monkeypatch.setattr(resident_record_domain, "find_resident", lambda _resident_id: record)
    class Catalog:
        def get_many(self, resident_ids):
            return [record] if resident_id in resident_ids else []

    monkeypatch.setattr(resident_catalog, "get_catalog", lambda: Catalog())
    monkeypatch.setattr(
        resident_directory_domain,
        "ensure_resident_visible_to_actor",
        lambda *_args, **_kwargs: None,
    )
    repository = resident_conversation_stream.get_repository()
    repository.state_store.database.write(
        lambda connection: connection.execute(
            "INSERT INTO residents(resident_id, title, backend, "
            "created_at, updated_at, created_by, owned_workspace_id) "
            "VALUES (%s, '', 'codex', NULL, NULL, '', %s) "
            "ON CONFLICT DO NOTHING",
            (resident_id, register_owned_workspace(connection, resident_id)),
        )
    )
    for index in range(11):
        repository.append_message(
            resident_id,
            {
                "message_id": f"message_{index}",
                "role": "user",
                "content_parts": [{"type": "text", "text": str(index)}],
                "author_id": "sample_admin",
                "created_at": f"2026-08-23T00:{index:02d}:00Z",
            },
        )
    monkeypatch.setattr(
        resident_conversation_projection_domain,
        "resident_conversation_messages",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("turn-count lookup must not read transcripts")
        ),
    )

    assert backend.get_resident_user_message_counts([resident_id], "sample_admin") == {
        resident_id: 11,
    }


def test_unchanged_hatg_membership_skips_resource_and_credential_projection(
    monkeypatch,
) -> None:
    """HATG 成员未变化时同步成员凭据不加载 Resident 记录"""
    resident_id = "resident_0123456789ab"
    lineage = {
        "lineage_id": "lineage_0123456789ab",
        "leader_resident_id": resident_id,
        "nodes": [{"id": resident_id}],
    }

    class Catalog:
        @staticmethod
        def get_many(_resident_ids):
            raise AssertionError("unchanged membership must not load Resident records")

    monkeypatch.setattr(resident_catalog, "get_catalog", lambda: Catalog())

    backend.sync_lineage_member_credentials(
        lineage,
        previous_lineage=dict(lineage),
    )


def test_workspace_authorization_resolves_only_path_candidates(
    tmp_path,
    monkeypatch,
) -> None:
    """Workspace 路径授权只判定包含该路径的那个 Workspace，不逐个检查 Resident"""
    from management_portal.society_service.state_store import get_lineage_data_store
    from management_portal.resources.workspace_store import get_workspace_store

    target_root = tmp_path / "target"
    target = "resident_ffffffffffff"
    workspaces = get_workspace_store()

    def seed(connection) -> None:
        # Fifty Residents own unrelated Workspaces; the target reaches the
        # path only through an extra Binding.
        for index in range(51):
            resident_id = target if index == 50 else f"resident_{index:012x}"
            owned = workspaces.ensure(
                f"workspace_{index:012x}", str(tmp_path / f"unrelated-{index}"), connection=connection,
            )
            connection.execute(
                "INSERT INTO residents(resident_id, owned_workspace_id) VALUES (%s, %s)",
                (resident_id, owned),
            )
        bound = workspaces.ensure("workspace_ffffffffffff", str(target_root), connection=connection)
        connection.execute(
            "INSERT INTO resident_workspace_bindings(resident_id, workspace_id, purpose) VALUES (%s, %s, '')",
            (target, bound),
        )

    get_lineage_data_store().database.write(seed)
    resolved: list[str] = []
    monkeypatch.setattr(
        resident_workspace_domain,
        "resolve_resident_identity",
        lambda resident_id, _actor: resolved.append(resident_id) or {"resident_id": resident_id},
    )
    from management_portal import permission_system

    checked: list[str] = []
    monkeypatch.setattr(
        permission_system,
        "permitted",
        lambda _actor, _instruction, _parameter, ids, **_options: checked.extend(ids) or [(item, True) for item in ids],
    )

    authorization = ResidentDomainHost.authorize_workspace_path(
        target_root / "artifact.txt",
        "sample_admin",
        write=True,
    )

    assert authorization["root"] == target_root.resolve(strict=False)
    assert resolved == []
    assert checked == ["workspace_ffffffffffff"]
