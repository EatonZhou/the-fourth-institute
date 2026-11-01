from __future__ import annotations
from pathlib import Path

import pytest

from management_portal import agent_token_auth
from management_portal.society_service import (
    resident_hatg_credentials,
)


def _seed(config):
    store = agent_token_auth.get_agent_token_store()
    for agent_id, record in config.items():
        store.put(agent_id, record)


RESIDENT_ID = "resident_aaaaaaaaaaaa"
LINEAGE_ID = "lineage_bbbbbbbbbbbb"


def _deleted_record(store, resident_id=RESIDENT_ID):
    from tests.cases.canonical_domain_fixture import canonical_projection

    canonical_projection(resident_id, lineage_id=LINEAGE_ID)
    record = {**store.resident_record(resident_id), "deleted_at": "2026-11-01T00:00:00Z"}
    store.remove_residents([record])
    revision = store.enqueue_resident_resource_projection(resident_id, record)
    return record, revision


@pytest.fixture()
def credential_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    token_root = tmp_path / "resident_hatg"
    monkeypatch.setattr(
        resident_hatg_credentials,
        "TOKEN_ROOT",
        token_root,
    )
    return token_root


def test_portal_provisions_a_stable_resident_hatg_bearer(
    credential_store: Path,
) -> None:
    """Portal 为 Resident 签发的 HATG bearer 文件权限为 600，重复调用返回同一 token"""
    first = resident_hatg_credentials.ensure_resident_credential(RESIDENT_ID, LINEAGE_ID)
    assert first is not None
    first_token = first.read_text(encoding="utf-8").strip()
    assert first_token
    assert first.stat().st_mode & 0o777 == 0o600
    auth = agent_token_auth.authenticate_bearer_token(first_token)
    assert auth is not None
    assert auth["agent_id"] == RESIDENT_ID

    second = resident_hatg_credentials.ensure_resident_credential(RESIDENT_ID, LINEAGE_ID)
    assert second == first
    assert second.read_text(encoding="utf-8").strip() == first_token


@pytest.mark.parametrize("change", ["disable", "rotate", "missing_file"])
def test_existing_credential_is_never_reissued(
    credential_store: Path, change: str,
) -> None:
    """已有凭据被停用、轮换或文件丢失时 Portal 不重新签发

    :design: decision_fb910c43ab07"""
    path = resident_hatg_credentials.ensure_resident_credential(RESIDENT_ID, LINEAGE_ID)
    token = path.read_text(encoding="utf-8").strip()
    store = agent_token_auth.get_agent_token_store()
    record = store.get(RESIDENT_ID)
    if change == "disable":
        record["enabled"] = False
    elif change == "rotate":
        replacement = agent_token_auth.generate_token()
        record.update(token_hash=agent_token_auth.hash_token(replacement), token_lookup=agent_token_auth.token_lookup(replacement))
    else:
        path.unlink()
    store.put(RESIDENT_ID, record)
    assert resident_hatg_credentials.ensure_resident_credential(RESIDENT_ID, LINEAGE_ID) is None
    assert store.get(RESIDENT_ID) == record
    if change != "missing_file":
        assert path.read_text(encoding="utf-8").strip() == token
        assert agent_token_auth.authenticate_bearer_token(token) is None
    if change == "rotate":
        assert agent_token_auth.authenticate_bearer_token(replacement)["agent_id"] == RESIDENT_ID


def test_portal_does_not_rotate_a_manually_managed_agent_token(
    credential_store: Path,
) -> None:
    """手工管理的 agent token 不被 Portal 轮换或接管"""
    manual_token = agent_token_auth.generate_token()
    _seed({
        RESIDENT_ID: {
            "token_hash": agent_token_auth.hash_token(manual_token),
            "enabled": True,
        }
    })
    result = resident_hatg_credentials.ensure_resident_credential(RESIDENT_ID, LINEAGE_ID)
    assert result is None
    stored = agent_token_auth.get_agent_token_store().list()
    assert stored[RESIDENT_ID].get("managed_by") is None
    assert agent_token_auth.verify_token_hash(stored[RESIDENT_ID]["token_hash"], manual_token)


def test_deleted_resident_bearer_stops_authenticating(
    credential_store: Path,
) -> None:
    """撤销后 Resident 的 bearer 不再通过认证、token 文件被删除，重复撤销无变化"""
    path = resident_hatg_credentials.ensure_resident_credential(RESIDENT_ID, LINEAGE_ID)
    token = path.read_text(encoding="utf-8").strip()
    assert agent_token_auth.authenticate_bearer_token(token) is not None

    assert resident_hatg_credentials.revoke_resident_credential(RESIDENT_ID) is True
    assert agent_token_auth.authenticate_bearer_token(token) is None
    assert not path.exists()
    record = agent_token_auth.get_agent_token_store().get(RESIDENT_ID)
    assert record is not None and record["enabled"] is False
    # Revoking again changes nothing; malformed IDs are ignored.
    assert resident_hatg_credentials.revoke_resident_credential(RESIDENT_ID) is False
    assert resident_hatg_credentials.revoke_resident_credential("not-a-resident") is False


def test_resident_deletion_confirmation_disables_the_bearer(
    credential_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """确认 Resident 删除时立即停用其 bearer，墓碑发布仍排期执行"""
    from management_portal.society_service import invariant_locks, resident_workspace_domain
    from management_portal.society_service.state_store import get_lineage_data_store

    record, revision = _deleted_record(get_lineage_data_store())
    path = resident_hatg_credentials.ensure_resident_credential(RESIDENT_ID, LINEAGE_ID)
    token = path.read_text(encoding="utf-8").strip()
    deferred = []
    monkeypatch.setattr(invariant_locks, "defer_until_state_unlock", deferred.append)
    resident_workspace_domain._confirm_deleted_resident_resources(
        [record], {RESIDENT_ID: revision},
    )
    assert agent_token_auth.authenticate_bearer_token(token) is None
    assert len(deferred) == 1  # the tombstone publication is still scheduled


@pytest.mark.parametrize("rollback", [False, True])
def test_deletion_revokes_bearer_only_after_commit_before_deferred_projection(
    credential_store: Path, monkeypatch: pytest.MonkeyPatch, rollback: bool,
) -> None:
    """提交即撤销 bearer；回滚保留凭据，投影解锁回调不控制认证失效。"""
    from management_portal.society_service import invariant_locks, resident_workspace_domain
    from management_portal.society_service.state_store import get_lineage_data_store

    path = resident_hatg_credentials.ensure_resident_credential(RESIDENT_ID, LINEAGE_ID)
    token = path.read_text(encoding="utf-8").strip()
    deferred = []
    monkeypatch.setattr(invariant_locks, "defer_until_state_unlock", deferred.append)

    class Rollback(Exception):
        pass

    try:
        with get_lineage_data_store().database.transaction():
            record, revision = _deleted_record(get_lineage_data_store())
            resident_workspace_domain._confirm_deleted_resident_resources(
                [record], {RESIDENT_ID: revision},
            )
            assert agent_token_auth.authenticate_bearer_token(token) is not None
            assert path.exists() and deferred == []
            if rollback:
                raise Rollback()
    except Rollback:
        pass

    assert (agent_token_auth.authenticate_bearer_token(token) is not None) == rollback
    assert path.exists() == rollback
    assert len(deferred) == (0 if rollback else 1)
    assert get_lineage_data_store().resident_resource_projection_pending(RESIDENT_ID) == (not rollback)


@pytest.mark.parametrize("failure", ["token_store", "unlink"])
def test_post_commit_revocation_attempts_every_resident_and_reports_failure(
    credential_store, monkeypatch, failure, caplog,
):
    """首项存储或 unlink 失败仍撤销整批其余凭据，失败不伪装为成功。"""
    from management_portal.society_service import invariant_locks, resident_workspace_domain
    from management_portal.society_service.state_store import get_lineage_data_store

    ids = [RESIDENT_ID, "resident_cccccccccccc", "resident_dddddddddddd"]
    paths = [resident_hatg_credentials.ensure_resident_credential(rid, LINEAGE_ID) for rid in ids]
    tokens = [path.read_text().strip() for path in paths]
    deferred = []
    monkeypatch.setattr(invariant_locks, "defer_until_state_unlock", deferred.append)
    original_put = agent_token_auth.AgentTokenStore.put
    original_unlink = Path.unlink

    def put(self, rid, record):
        if rid == ids[0] and record.get("enabled") is False:
            raise RuntimeError("injected token store failure")
        return original_put(self, rid, record)

    def unlink(path, *args, **kwargs):
        if path == paths[0]:
            raise PermissionError("injected unlink failure")
        return original_unlink(path, *args, **kwargs)

    if failure == "token_store":
        monkeypatch.setattr(agent_token_auth.AgentTokenStore, "put", put)
    else:
        monkeypatch.setattr(Path, "unlink", unlink)
    store = get_lineage_data_store()
    with store.database.transaction():
        deletions = [_deleted_record(store, rid) for rid in ids]
        resident_workspace_domain._confirm_deleted_resident_resources(
            [record for record, _ in deletions],
            {record["resident_id"]: revision for record, revision in deletions},
        )
        assert all(agent_token_auth.authenticate_bearer_token(token) for token in tokens)
    assert all(agent_token_auth.authenticate_bearer_token(token) is None for token in tokens[1:])
    assert (agent_token_auth.authenticate_bearer_token(tokens[0]) is not None) == (failure == "token_store")
    assert len(deferred) == 1
    assert "injected" in caplog.text
    assert all(store.resident_resource_projection_pending(rid) for rid in ids)


@pytest.mark.parametrize("entry", ["batch", "explicit_retry"])
def test_deletion_confirmation_never_revokes_a_live_resident(credential_store, monkeypatch, entry):
    """确认入口不得将活对象或过时请求当作真实墓碑撤销。"""
    from management_portal.society_service import invariant_locks, resident_workspace_domain
    from management_portal.society_service.state_store import get_lineage_data_store
    from tests.cases.canonical_domain_fixture import canonical_projection

    canonical_projection(RESIDENT_ID, lineage_id=LINEAGE_ID)
    store = get_lineage_data_store()
    path = resident_hatg_credentials.ensure_resident_credential(RESIDENT_ID, LINEAGE_ID)
    token = path.read_text().strip()
    monkeypatch.setattr(invariant_locks, "defer_until_state_unlock", lambda callback: None)
    if entry == "batch":
        resident_workspace_domain._confirm_deleted_resident_resources(
            [{**store.resident_record(RESIDENT_ID), "deleted_at": "2026-11-01T00:00:00Z"}],
            {RESIDENT_ID: 1},
        )
    else:
        resident_workspace_domain.confirm_resident_deletion_projection(RESIDENT_ID)
    assert agent_token_auth.authenticate_bearer_token(token) is not None
    assert path.exists()


@pytest.mark.parametrize("collect", [False, True])
def test_failed_immediate_revocation_preserves_committed_receipt(credential_store, monkeypatch, collect):
    """立即撤销失败仍给出已提交、投影待处理的回执，并安排后续投影。"""
    from management_common.projection_receipts import CommittedProjectionError, collect_projection_failures
    from management_portal.society_service import invariant_locks, resident_workspace_domain
    from management_portal.society_service.state_store import get_lineage_data_store

    record, revision = _deleted_record(get_lineage_data_store())
    resident_hatg_credentials.ensure_resident_credential(RESIDENT_ID, LINEAGE_ID)
    deferred = []
    monkeypatch.setattr(invariant_locks, "defer_until_state_unlock", deferred.append)

    def unavailable(*args):
        raise OSError("credential store unavailable")

    monkeypatch.setattr(resident_hatg_credentials, "revoke_resident_credential", unavailable)
    if collect:
        with collect_projection_failures() as failures:
            resident_workspace_domain._confirm_deleted_resident_resources([record], {RESIDENT_ID: revision})
    else:
        with pytest.raises(CommittedProjectionError) as error:
            resident_workspace_domain._confirm_deleted_resident_resources([record], {RESIDENT_ID: revision})
        assert error.value.commit_state == "committed"
        failures = error.value.failures
    assert len(failures) == 1
    assert failures[0]["resident_id"] == RESIDENT_ID
    assert failures[0]["committed_revision"] == revision
    assert failures[0]["error"] == "credential store unavailable"
    assert len(deferred) == 1
