"""Mailbox recovery through the complete production coordinator, store and DOM.

:module: resident.conversation
:importance: P1
:aspects: ui, behavior
:design: decision_18abbdef3da3
"""
from pathlib import Path
import subprocess


def _run(name):
    source = Path(__file__).with_name("javascript") / "message_state_mailbox.cjs"
    result = subprocess.run(["node", str(source), name], capture_output=True, text=True, timeout=30)
    print(result.stdout, end="")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == f"PASS {name}\n"


def test_realtime_disconnect_verifies_snapshot_without_false_error():
    """实时连接断开后 Mailbox 重新核对快照，不留下误报的刷新错误

    Mailbox verifies its snapshot after realtime disconnect without keeping a false refresh error

    Stable case test_6ae8121e0b7a.

    :design-point:
    """
    _run("realtime-disconnect")


def test_transient_snapshot_failure_retries_and_clears_banner():
    """Mailbox 快照读取暂时失败时自动重试，恢复后清除错误横幅

    Mailbox retries a transient snapshot failure and clears the banner after recovery

    Stable case test_2543711f162b.

    :design-point:
    """
    _run("transient-retry")


def test_background_index_budget_preserves_queued_and_newer_events():
    """Background index reads reserve control capacity and eventually apply every queued update."""
    _run("background-index-budget")


def test_mailbox_read_provenance_preserves_coalescing_errors_and_privacy():
    """Actual index dispatches retain content-free causal receipts without extra reads."""
    _run("read-provenance")


def test_missing_resident_state_stays_unknown_without_failing_mailbox():
    """A valid omission clears stale per-target state while other native rows remain usable."""
    _run("state-omission")


def test_resident_context_names_survive_refresh_without_redundant_source_labels():
    """Mailbox shows each Resident's Lineage name and keeps other source labels intact."""
    _run("resident-context")


def test_refresh_removes_obsolete_cached_sources_without_losing_resident_pages():
    """A complete source snapshot removes cached ghost unread while pagination preserves live sources."""
    _run("refresh-removes-obsolete-sources")


def test_clear_reconciles_obsolete_sources_when_server_has_nothing_to_clear():
    """A no-op clear receipt triggers one fresh source read to remove local ghost unread."""
    _run("clear-reconciles-obsolete-sources")
