from __future__ import annotations

import subprocess

import pytest


def test_installer_commands_have_finite_timeout_and_owned_tree(monkeypatch, tmp_path):
    from scripts import tfi_release_install as installer

    calls = []
    monkeypatch.setattr(installer, "clean_environment", lambda target, **kwargs: {})

    def fake_run(command, **kwargs):
        calls.append(kwargs)
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(installer, "run_utf8_capture", fake_run)
    with pytest.raises(TimeoutError, match="process tree was stopped"):
        installer.run_checked(["tool"], target={"install_root": str(tmp_path)})
    assert calls[0]["timeout"] == 1800


def test_native_environment_file_is_loaded_at_service_start(tmp_path):
    from scripts.portal_native_service_child import load_environment_files

    path = tmp_path / "private settings.env"
    path.write_text("# comment\nexport PORTAL_DATABASE_URL='postgresql://host/db name'\n"
                    "PORTAL_DATA_PATH='C:\\Program Files\\Portal'\n", encoding="utf-8")
    result = load_environment_files([path], {"UNCHANGED": "yes"})
    assert result["PORTAL_DATABASE_URL"] == "postgresql://host/db name"
    assert result["PORTAL_DATA_PATH"] == r"C:\Program Files\Portal"
    assert result["UNCHANGED"] == "yes"
