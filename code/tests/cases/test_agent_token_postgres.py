from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from management_portal import agent_token_auth


def test_postgres_agent_registry_is_atomic_across_connections():
    """PostgreSQL 上的 Agent token 注册表跨多个连接原子更新，回滚后各连接读到一致结果"""
    stores = []
    try:
        for number in range(4):
            stores.append(agent_token_auth.AgentTokenStore())
        stores[0].put("counter", {"expires_at": 0})

        def increment(number):
            store = stores[number % len(stores)]
            with store.transaction("counter"):
                record = store.get("counter")
                store.put("counter", {"expires_at": record["expires_at"] + 1})

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(increment, range(24)))
        assert stores[0].get("counter") == {"token_lookup": "", "expires_at": 24}
        with pytest.raises(RuntimeError):
            with stores[0].transaction("counter"):
                stores[0].put("counter", {"expires_at": -1})
                raise RuntimeError("rollback")
        assert stores[1].get("counter") == {"token_lookup": "", "expires_at": 24}
        stores[0].put("indexed", {"token_lookup": "sha256:test", "enabled": False})
        assert stores[1].candidates("sha256:test") == [("indexed", {"token_lookup": "sha256:test", "enabled": False})]
    finally:
        for store in stores:
            store.database.close()
