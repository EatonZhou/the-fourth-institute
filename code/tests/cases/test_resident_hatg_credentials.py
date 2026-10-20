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

    path = resident_hatg_credentials.ensure_resident_credential(RESIDENT_ID, LINEAGE_ID)
    token = path.read_text(encoding="utf-8").strip()
    deferred = []
    monkeypatch.setattr(invariant_locks, "defer_until_state_unlock", deferred.append)
    resident_workspace_domain._confirm_deleted_resident_resources(
        [{"resident_id": RESIDENT_ID, "lineage_id": LINEAGE_ID}], {RESIDENT_ID: 1},
    )
    assert agent_token_auth.authenticate_bearer_token(token) is None
    assert len(deferred) == 1  # the tombstone publication is still scheduled
