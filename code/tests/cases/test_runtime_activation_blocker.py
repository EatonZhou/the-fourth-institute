"""Unsupported native exclusion cannot fall through to global link activation.

:module: execution.service
"""
from management_portal.execution import provider_updates as updates
from types import SimpleNamespace

import pytest


def test_codex_unverified_native_exclusion_never_flips_link_or_restarts(monkeypatch, tmp_path):
    runtime = updates.CodexRuntime()
    monkeypatch.setattr(updates, "_active_executions", lambda _: (_ for _ in ()).throw(AssertionError("no global queue proof")))
    monkeypatch.setattr(updates, "_link", lambda *args: (_ for _ in ()).throw(AssertionError("no link mutation")))
    assert runtime.activation_blocker == "native_admission_exclusivity_unverified"
    assert updates._activate("codex", runtime, tmp_path) is False


@pytest.mark.parametrize("pending", [False, True])
def test_runtime_status_only_exposes_blocker_for_pending_codex(monkeypatch, tmp_path, pending):
    codex = updates.CodexRuntime()
    monkeypatch.setattr(updates.CodexRuntime, "installed", lambda _: ("0.167.2",))
    other = SimpleNamespace(installed=lambda: ("1.0",))
    monkeypatch.setattr(updates, "runtimes", lambda: {"codex": codex, "claude": other})
    monkeypatch.setattr(updates, "_read_state", lambda: {
        "pending": {"codex": str(tmp_path / "staged"), "claude": str(tmp_path / "other")} if pending else {},
        "observations": {"codex": {"available_version": "0.169.0", "checked_at": "observed", "check_error": ""}}})
    monkeypatch.setattr(updates, "run_command", lambda *a, **kw: pytest.fail("read projection must not execute programs"))
    monkeypatch.setattr(updates, "_link", lambda *a: pytest.fail("read projection must not mutate links"))
    result = updates.provider_runtime_status()
    assert result["codex"]["installed_version"] == "0.167.2"
    assert result["codex"]["available_version"] == "0.169.0"
    assert result["codex"]["pending_switch"] is pending
    assert result["codex"].get("activation_blocker", "") == (codex.activation_blocker if pending else "")
    assert "activation_blocker" not in result["claude"]
    assert result["claude"]["pending_switch"] is pending


def test_non_codex_activation_preserves_previous_flow_without_real_services(monkeypatch, tmp_path):
    before = sorted(tmp_path.iterdir())
    calls = []
    controller = SimpleNamespace(restart=lambda name: calls.append(("restart", name)),
        status=lambda _: {"active": "active"})
    runtime = SimpleNamespace(busy=lambda: False, services=("fake-service",),
        account_processes=lambda: ("fake-account",), service_control=lambda: controller,
        activation_link=lambda: tmp_path / "never-created-link", link_target=lambda root: root / "fake-program",
        restart_account_process=lambda account: calls.append(("account", account)),
        ensure_ready=lambda: calls.append(("ready",)))
    monkeypatch.setattr(updates, "_active_executions", lambda provider: calls.append(("active", provider)) or 0)
    monkeypatch.setattr(updates, "_link", lambda link, target: calls.append(("link", link, target)))
    assert updates._activate("claude", runtime, tmp_path / "staged", prepare=True)
    assert calls == [("active", "claude"),
        ("link", tmp_path / "never-created-link", tmp_path / "staged" / "fake-program"),
        ("restart", "fake-service"), ("account", "fake-account"), ("ready",)]
    assert sorted(tmp_path.iterdir()) == before
