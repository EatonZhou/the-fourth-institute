from __future__ import annotations

import pytest

from management_portal.society_service import lineage_domain


LINEAGE = "lineage_111111111111"
SOURCE = "resident_222222222222"


def test_default_fork_mode_is_accepted_by_branch_contract(fork_payloads):
    """ForkResident 未指定模式时按 full 分叉"""
    from management_portal.society_service.lineage_branch_transitions import branch_request

    fork()
    assert branch_request(fork_payloads[0])[-1] == "full"


@pytest.fixture()
def fork_payloads(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    payloads: list[dict] = []

    def create_branch(lineage_id: str, payload: dict, actor: str) -> dict:
        assert lineage_id == LINEAGE
        assert actor in {"sample_admin", "another-owner"}
        payloads.append(payload)
        return {"edge_id": payload["edge_id"]}

    monkeypatch.setattr(lineage_domain, "create_lineage_branch", create_branch)
    return payloads


def fork(**arguments: object) -> dict:
    return lineage_domain.fork_lineage_resident(
        LINEAGE, {"source": SOURCE, **arguments}, "sample_admin"
    )


def test_distinct_instruction_keys_create_distinct_fork_edges(fork_payloads: list[dict]) -> None:
    """不同的幂等键创建不同的分叉边"""
    first = fork(idempotency_key="audit-fork-01")
    second = fork(idempotency_key="audit-fork-02")
    assert first["edge_id"] != second["edge_id"]
    assert len(fork_payloads) == 2


def test_fork_retry_reuses_instruction_identity(fork_payloads: list[dict]) -> None:
    """带相同幂等键的重试沿用同一分叉边，期望 revision 不同也一样"""
    first = fork(idempotency_key="audit-fork-retry", expected_revision=1)
    retry = fork(idempotency_key="audit-fork-retry", expected_revision=2)
    assert first["edge_id"] == retry["edge_id"]
    assert len(fork_payloads) == 2


def test_fork_instruction_key_is_scoped_to_its_principal(fork_payloads: list[dict]) -> None:
    """幂等键按调用者区分，不同调用者用同一个键得到不同分叉边"""
    first = fork(idempotency_key="shared-key")
    second = lineage_domain.fork_lineage_resident(
        LINEAGE, {"source": SOURCE, "idempotency_key": "shared-key"}, "another-owner"
    )
    assert first["edge_id"] != second["edge_id"]
    assert len(fork_payloads) == 2


def test_unkeyed_independent_forks_do_not_reuse_source_as_identity(fork_payloads: list[dict]) -> None:
    """不带幂等键的独立分叉各得新边，不以来源 Resident 作为身份"""
    first = fork()
    second = fork()
    assert first["edge_id"] != second["edge_id"]
    assert len(fork_payloads) == 2


def test_explicit_fork_edge_is_preserved(fork_payloads: list[dict]) -> None:
    """显式指定的分叉边 ID 在再次分叉时保留"""
    first = fork(edge_id="edge_explicit_fork", idempotency_key="audit-first")
    retry = fork(edge_id="edge_explicit_fork", idempotency_key="audit-next")
    assert first["edge_id"] == retry["edge_id"] == "edge_explicit_fork"
    assert len(fork_payloads) == 2


def test_run_scoped_fork_retries_keep_their_existing_edge(fork_payloads: list[dict]) -> None:
    """同一次运行中 Effect 的分叉重试沿用原分叉边，下一次运行得到新边"""
    first = fork(effect_id="effect_a", trigger_path_id="run_a")
    retry = fork(effect_id="effect_a", trigger_path_id="run_a")
    following = fork(effect_id="effect_a", trigger_path_id="run_b")
    assert first["edge_id"] == retry["edge_id"]
    assert first["edge_id"] != following["edge_id"]
    assert len(fork_payloads) == 3
