from __future__ import annotations

from unittest import mock
from concurrent.futures import ThreadPoolExecutor

import pytest

from management_portal import agent_token_auth


def _seed(config):
    store = agent_token_auth.get_agent_token_store()
    for agent_id, record in config.items():
        store.put(agent_id, record)


def test_token_lookup_selects_one_pbkdf_candidate_contracts(tmp_path, subtests, isolated_portal_case):
    """Agent token 认证按 lookup 只校验一个 PBKDF 候选，缺少 lookup 的记录无法认证"""
    with subtests.test(contract='test_token_lookup_selects_one_pbkdf_candidate'), isolated_portal_case() as case_path:
        tmp_path = case_path
        token = "high-entropy-target-token"
        _seed(
            {
                "resident_aaaaaaaaaaaa": {
                    "token_hash": "unrelated-hash",
                    "token_lookup": agent_token_auth.token_lookup(
                        "another-token"
                    ),
                    "enabled": True,
                },
                "resident_bbbbbbbbbbbb": {
                    "token_hash": "target-hash",
                    "token_lookup": agent_token_auth.token_lookup(token),
                    "enabled": True,
                },
            },
        )

        with mock.patch.object(
            agent_token_auth,
            "verify_token_hash",
            return_value=True,
        ) as verify:
            auth = agent_token_auth.authenticate_bearer_token(token)

        assert auth is not None
        assert auth["agent_id"] == "resident_bbbbbbbbbbbb"
        assert set(auth) == {"agent_id"}
        verify.assert_called_once_with("target-hash", token)
    with subtests.test(contract='test_token_without_lookup_is_not_authenticatable'), isolated_portal_case() as case_path:
        tmp_path = case_path
        token = agent_token_auth.generate_token()
        _seed(
            {
                "incomplete-agent": {
                    "token_hash": agent_token_auth.hash_token(token),
                    "enabled": True,
                }
            },
        )

        auth = agent_token_auth.authenticate_bearer_token(token)

        assert auth is None


def test_registry_authentication_rotation_disable_and_metadata():
    """Agent token 轮换后旧 token 失效，禁用后新 token 也无法认证"""
    store = agent_token_auth.get_agent_token_store()
    original = "original-test-token"
    replacement = "replacement-test-token"
    record = {"token_hash": agent_token_auth.hash_token(original),
              "token_lookup": agent_token_auth.token_lookup(original),
              "enabled": True, "managed_by": "manual"}
    store.put("test-agent", record)
    assert agent_token_auth.authenticate_bearer_token(original) == {"agent_id": "test-agent"}
    assert agent_token_auth.authenticate_bearer_token("wrong-token") is None
    assert agent_token_auth.authenticate_bearer_token("") is None
    store.put("test-agent", {**record, "token_hash": agent_token_auth.hash_token(replacement),
                            "token_lookup": agent_token_auth.token_lookup(replacement)})
    assert agent_token_auth.authenticate_bearer_token(original) is None
    assert agent_token_auth.authenticate_bearer_token(replacement)["agent_id"] == "test-agent"
    store.put("test-agent", {**store.get("test-agent"), "enabled": False})
    assert agent_token_auth.authenticate_bearer_token(replacement) is None


def test_registry_lookup_is_indexed_and_does_not_read_full_registry(monkeypatch):
    """Agent token 认证走 agent_tokens_lookup 索引，不枚举整个注册表"""
    store = agent_token_auth.get_agent_token_store()
    store.put("test-agent", {"token_lookup": agent_token_auth.token_lookup("test-token"),
                             "token_hash": "invalid-hash"})
    monkeypatch.setattr(store, "list", lambda: pytest.fail("Authentication must not enumerate registry"))
    assert agent_token_auth.authenticate_bearer_token("test-token") is None
    with store.database.read_transaction() as connection:
        connection.execute("SET LOCAL enable_seqscan=off")
        plan = connection.execute(
            'EXPLAIN (COSTS OFF) SELECT token_hash FROM agent_tokens WHERE token_lookup = %s',
            ("test",),
        ).fetchall()
    assert any("agent_tokens_lookup" in str(row[0]) for row in plan)


def test_registry_atomic_updates_and_rollback(tmp_path):
    """Agent token 注册表事务内更新原子执行，异常时回滚，未知字段被拒绝"""
    store = agent_token_auth.get_agent_token_store()
    store.put("lease", {"expires_at": 0})

    def increment(unused):
        with store.transaction("lease"):
            store.put("lease", {"expires_at": store.get("lease")["expires_at"] + 1})

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(increment, range(20)))
    assert store.get("lease")["expires_at"] == 20
    with pytest.raises(RuntimeError):
        with store.transaction("lease"):
            store.put("lease", {"expires_at": -1})
            raise RuntimeError("rollback")
    assert store.get("lease")["expires_at"] == 20
    with pytest.raises(ValueError, match="Unknown Agent authentication fields"):
        store.put("lease", {"value": 0})


def test_database_error_cannot_become_empty_registry(monkeypatch):
    """数据库出错时 Agent token 认证抛出错误，不当作空注册表"""
    store = agent_token_auth.get_agent_token_store()

    def unavailable(lookup):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(store, "candidates", unavailable)
    with pytest.raises(RuntimeError, match="database unavailable"):
        agent_token_auth.authenticate_bearer_token("test-token")


def test_registry_requires_postgres_configuration(monkeypatch):
    """未配置 PORTAL_DATABASE_URL 时无法创建 Agent token 注册表"""
    monkeypatch.delenv("PORTAL_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="requires PORTAL_DATABASE_URL"):
        agent_token_auth.AgentTokenStore()
