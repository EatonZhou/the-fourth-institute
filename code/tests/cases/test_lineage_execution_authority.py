from __future__ import annotations

from threading import Event, Thread

from management_portal.society_service.lineage_execution_authority import execution_authority
from management_portal.society_service import lineage_execution_authority as authority


def test_program_authority_serializes_concurrent_takeovers(tmp_path):
    """同一 Lineage 的执行权可重入，并发接管者等持有者释放后才进入"""
    holder_entered = Event()
    release_holder = Event()
    contender_attempted = Event()
    contender_entered = Event()

    def holder():
        with execution_authority("lineage_111111111111", root=tmp_path):
            with execution_authority("lineage_111111111111", root=tmp_path):
                holder_entered.set()
                release_holder.wait(5)

    def contender():
        contender_attempted.set()
        with execution_authority("lineage_111111111111", root=tmp_path):
            contender_entered.set()

    first = Thread(target=holder)
    second = Thread(target=contender)
    first.start()
    try:
        assert holder_entered.wait(5)
        second.start()
        assert contender_attempted.wait(5)
        assert not contender_entered.wait(0.1)
    finally:
        release_holder.set()
        first.join(5)
        if second.ident is not None:
            second.join(5)
    assert not first.is_alive()
    assert not second.is_alive()
    assert contender_entered.is_set()


def test_wait_reports_holder_stack_without_releasing_authority(tmp_path, monkeypatch, caplog):
    """等待执行权过久时日志报告持有者线程和调用栈，执行权不被强行释放"""
    monkeypatch.setattr(authority, "_WAIT_DIAGNOSTIC_SECONDS", 0.01)
    holder_entered, release_holder, logged, contender_entered = (Event() for _ in range(4))
    warning = authority._LOGGER.warning

    def report(*args):
        warning(*args)
        logged.set()

    monkeypatch.setattr(authority._LOGGER, "warning", report)

    def slow_owner():
        with execution_authority("lineage_222222222222", root=tmp_path):
            holder_entered.set()
            release_holder.wait(5)

    def contender():
        with execution_authority("lineage_222222222222", root=tmp_path):
            contender_entered.set()

    first, second = Thread(target=slow_owner), Thread(target=contender)
    first.start()
    try:
        assert holder_entered.wait(5)
        second.start()
        assert logged.wait(5)
        assert not contender_entered.is_set()
        assert "lineage_222222222222" in caplog.text
        assert "slow_owner" in caplog.text
        assert str(first.ident) in caplog.text
    finally:
        release_holder.set()
        first.join(5)
        if second.ident is not None:
            second.join(5)
    assert contender_entered.is_set()
    assert "lineage_222222222222" not in authority._OWNERS
