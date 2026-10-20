import pytest
from management_common import site_config
from management_portal.execution import account_login
from management_portal.hatg.contracts import InstructionError


def test_explicit_provider_binary_works_without_host_path(tmp_path, monkeypatch):
    binary = tmp_path / "codex"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o700)
    monkeypatch.setattr(site_config, "read_site_config", lambda: {
        "deployment": {"providers": {"codex_binary": str(binary)}}})
    monkeypatch.setattr(account_login.shutil, "which", lambda _: None)
    assert account_login.login_command("codex", status=True) == [str(binary), "login", "status"]
    binary.unlink()
    with pytest.raises(InstructionError):
        account_login.login_command("codex", status=True)
