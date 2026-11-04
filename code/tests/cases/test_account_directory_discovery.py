"""Account discovery is metadata-only; reuse must not turn into new creation."""
from pathlib import Path
from types import SimpleNamespace
import json
import pytest

from management_common import host_instances, provider_account_paths as paths
from management_portal.execution import account_directory_discovery as discovery
from management_portal.execution import account_registrations as store, isa
from management_portal.hatg.contracts import InstructionError
from tests.cases.isa_test_support import run, ok, reset_kernels


@pytest.fixture(autouse=True)
def isolated_host(tmp_path, monkeypatch):
    monkeypatch.setattr(host_instances, "registry_path", lambda: tmp_path / "registry.json")
    monkeypatch.setattr(discovery.Path, "home", classmethod(lambda cls: tmp_path))
    reset_kernels()
    yield
    reset_kernels()


def marker(directory, name="auth.json"):
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    (directory / name).write_text("SYNTHETIC_SECRET_DO_NOT_RETURN", encoding="utf-8")
    return directory


def scan(home, provider="codex", env=None, excluded=()):
    return discovery.discover(provider, home=home, environ=env or {}, excluded=excluded)


def test_discovery_deduplicates_roots_excludes_registered_and_reads_no_credentials(tmp_path, monkeypatch):
    direct = marker(tmp_path / ".codex")
    managed = marker(tmp_path / ".tfi-provider-accounts/instance_one/codex/one")
    blocked = marker(tmp_path / ".codex-accounts/registered")
    marker(tmp_path / ".codex-accounts/unrelated", "config.toml")
    marker(tmp_path / "unrelated/auth-location")  # HOME itself is never recursive.
    monkeypatch.setattr(Path, "open", lambda *a, **kw: pytest.fail("Scanner read file contents"))
    result = scan(tmp_path, env={"CODEX_HOME": str(direct)}, excluded={str(blocked)})
    assert [c["path"] for c in result["candidates"]] == sorted([str(direct), str(managed)])
    assert all(set(c) == {"path", "provider", "evidence", "login_state"} for c in result["candidates"])
    assert all(c["login_state"] == "unverified" and "auth.json" in c["evidence"] for c in result["candidates"])
    assert "SYNTHETIC_SECRET" not in str(result)
    assert result["warnings"] == [] and result["truncated"] is False


def test_explicit_registered_paths_excluded_without_implicit_portal_reads(tmp_path, monkeypatch):
    direct = marker(tmp_path / ".codex")
    monkeypatch.setattr(store, "registrations", lambda: pytest.fail("Offline scanner read Portal registrations"))
    assert discovery.discover("codex", home=tmp_path, environ={}, excluded=[str(direct)])["candidates"] == []
    assert len(discovery.discover("codex", home=tmp_path, environ={})["candidates"]) == 1


@pytest.mark.parametrize("provider,name,filename,env", [
    ("codex", ".codex", "auth.json", "CODEX_HOME"),
    ("claude", ".claude", ".credentials.json", "CLAUDE_CONFIG_DIR"),
    ("antigravity", ".gemini", "oauth_creds.json", "GEMINI_HOME"),
    ("kimi", ".kimi", "config.toml", "KIMI_CODE_HOME"),
])
def test_provider_markers_and_explicit_environment_home(tmp_path, provider, name, filename, env):
    conventional = marker(tmp_path / name, filename)
    explicit = marker(tmp_path / "elsewhere/explicit", filename)
    rows = scan(tmp_path, provider, {env: str(explicit)})["candidates"]
    assert {r["path"] for r in rows} == {str(conventional), str(explicit)}
    assert all(r["provider"] == provider for r in rows)


@pytest.mark.parametrize("variable", ["APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME"])
def test_os_config_roots_are_narrow(tmp_path, variable):
    selected = marker(tmp_path / "configs/codex")
    marker(tmp_path / "configs/unrelated")
    rows = scan(tmp_path, env={variable: str(tmp_path / "configs")})["candidates"]
    assert [r["path"] for r in rows] == [str(selected)]


def test_symlink_directories_ancestors_and_markers_are_never_candidates(tmp_path):
    real = marker(tmp_path / "real")
    (tmp_path / ".codex").symlink_to(real, target_is_directory=True)
    container = tmp_path / ".codex-accounts"
    container.mkdir()
    (container / "alias").symlink_to(real, target_is_directory=True)
    fake = container / "fake"
    fake.mkdir()
    (fake / "auth.json").symlink_to(real / "auth.json")
    result = scan(tmp_path)
    assert not result["candidates"]
    assert result["warnings"]  # Explicit conventional alias is diagnosed.


def test_unreadable_scan_warns_but_does_not_claim_usable_candidate(tmp_path, monkeypatch):
    target = marker(tmp_path / ".codex")
    original = discovery.os.scandir
    def denied(path):
        if Path(path) == target:
            raise PermissionError("synthetic")
        return original(path)
    monkeypatch.setattr(discovery.os, "scandir", denied)
    result = scan(tmp_path)
    assert result["candidates"] == []
    assert result["warnings"] == [f"无法检查目录：{target}（PermissionError）"]


@pytest.mark.parametrize("limit", ["MAX_ENTRIES", "MAX_DIRECTORIES", "MAX_CANDIDATES"])
def test_scan_budgets_signal_truncation(tmp_path, monkeypatch, limit):
    for name in ("one", "two", "three"):
        marker(tmp_path / ".codex-accounts" / name)
    monkeypatch.setattr(discovery, limit, 1)
    result = scan(tmp_path)
    assert result["truncated"] is True
    if limit == "MAX_CANDIDATES":
        assert len(result["candidates"]) == 1


def test_depth_boundary_ignores_deep_material(tmp_path):
    marker(tmp_path / ".codex-accounts/a/b/c/deep")
    assert scan(tmp_path)["candidates"] == []


def test_browse_parent_roots_empty_and_only_directory_metadata(tmp_path):
    tmp_path = tmp_path / "browser"
    tmp_path.mkdir()
    empty = tmp_path / "empty"
    empty.mkdir()
    (tmp_path / "secret.json").write_text("synthetic")
    (tmp_path / "alias").symlink_to(empty, target_is_directory=True)
    result = discovery.browse(str(tmp_path))
    assert result == {"path": str(tmp_path), "parent": str(tmp_path.parent),
        "directories": [{"name": "empty", "path": str(empty)}],
        "roots": [{"name": "/", "path": "/"}], "truncated": False}
    assert discovery.browse(str(empty))["directories"] == []
    assert discovery.browse(str(empty))["parent"] == str(tmp_path)
    assert discovery.browse()["path"] == str(tmp_path.parent)


@pytest.mark.parametrize("value", ["", "relative", "missing", "link", "file"])
def test_browse_invalid_paths(tmp_path, value):
    (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
    (tmp_path / "file").write_text("synthetic")
    path = value if value in {"", "relative"} else str(tmp_path / value)
    with pytest.raises(InstructionError):
        discovery.browse(path)


def test_browse_denied_and_truncated(tmp_path, monkeypatch):
    for name in ("one", "two"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(discovery, "MAX_BROWSE_DIRECTORIES", 1)
    assert discovery.browse(str(tmp_path))["truncated"] is True
    monkeypatch.setattr(discovery.os, "scandir", lambda _: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(InstructionError) as error:
        discovery.browse(str(tmp_path))
    assert error.value.code == "instruction.forbidden"


@pytest.mark.parametrize("config", [None, {}, {"account_dir": ""}, {"account_dir": "relative"}, {"account_dir": "/definitely-missing-account-directory"}])
def test_real_isa_reuse_rejects_empty_and_missing_before_default_creation(tmp_path, monkeypatch, config):
    before = list(tmp_path.iterdir())
    monkeypatch.setattr(paths, "default_account_directory", lambda *a: pytest.fail("Reuse fell through to default"))
    result = run("RegisterProviderAccount", {"provider": "codex", "account_id": "codex_reuse",
        "kind": "subscription_login", "reuse_existing": True, **({"config": config} if config is not None else {})})
    with pytest.raises(InstructionError) as error:
        result.raise_for_error()
    assert error.value.code == "instruction.invalid_arguments"
    assert store.registration("codex_reuse") is None
    assert list(tmp_path.iterdir()) == before


def test_reuse_existing_real_isa_keeps_directory_and_uses_existing_setup(tmp_path, monkeypatch):
    directory = marker(tmp_path / "existing")
    inode = directory.stat().st_ino
    from management_portal.execution import provider_updates
    monkeypatch.setattr(provider_updates, "runtime_available", lambda _: False)
    monkeypatch.setattr(isa, "_registration_public", lambda row, caller: row)
    result = ok("RegisterProviderAccount", {"provider": "codex", "account_id": "codex_reuse",
        "kind": "subscription_login", "reuse_existing": True, "config": {"account_dir": str(directory)}})
    assert result["registration"]["config"]["account_dir"] == str(directory)
    assert directory.stat().st_ino == inode
    assert (directory / "auth.json").read_text() == "SYNTHETIC_SECRET_DO_NOT_RETURN"


def test_owner_rechecks_after_isa_preflight_and_never_mkdir(tmp_path, monkeypatch):
    directory = tmp_path / "existing"
    directory.mkdir(mode=0o700)
    prepare = paths.prepare_registration_directory
    def remove_before_owner(data, **kwargs):
        assert kwargs == {"require_existing": True}
        directory.rmdir()
        return prepare(data, **kwargs)
    monkeypatch.setattr(paths, "prepare_registration_directory", remove_before_owner)
    result = run("RegisterProviderAccount", {"provider": "codex", "account_id": "codex_reuse",
        "kind": "subscription_login", "reuse_existing": True, "config": {"account_dir": str(directory)}})
    with pytest.raises(InstructionError):
        result.raise_for_error()
    assert not directory.exists() and store.registration("codex_reuse") is None


def test_private_validation_missing_leaf_never_creates(tmp_path, monkeypatch):
    directory = tmp_path / "leaf"
    directory.mkdir(mode=0o700)
    original = paths.assert_private_directory
    def remove_before_private(path):
        path.rmdir()
        return original(path)
    monkeypatch.setattr(paths, "assert_private_directory", remove_before_private)
    with pytest.raises(FileNotFoundError):
        paths.prepare_registration_directory({"kind": "subscription_login", "config": {"account_dir": str(directory)}}, require_existing=True)
    assert not directory.exists()


@pytest.mark.parametrize("config", [{}, {"account_dir": ""}])
def test_registration_owner_cannot_default_reuse(tmp_path, monkeypatch, config):
    monkeypatch.setattr(paths, "default_account_directory", lambda *a: pytest.fail("Owner defaulted reuse"))
    with pytest.raises(InstructionError):
        store.create_registration({"provider": "codex", "account_id": "codex_reuse",
            "kind": "subscription_login", "config": config}, "test", require_existing=True)


def test_reuse_boolean_schema_and_kind_validation():
    with pytest.raises(InstructionError):
        ok("RegisterProviderAccount", {"provider": "codex", "account_id": "reuse",
            "kind": "subscription_login", "reuse_existing": "true"})
    with pytest.raises(InstructionError):
        discovery.validate_reuse_directory({"kind": "api_profile"})


def test_unknown_provider_does_not_scan(monkeypatch):
    monkeypatch.setattr(discovery.os, "scandir", lambda _: pytest.fail("Unsupported Provider scanned"))
    with pytest.raises(InstructionError):
        scan(Path("/"), provider="provider_api")


def test_windows_drive_roots_are_bounded_and_ignore_unavailable_drives(monkeypatch):
    class Drive:
        def __init__(self, value):
            self.value = value
        def is_dir(self):
            if self.value.startswith("D"):
                raise PermissionError()
            return self.value.startswith("C")
        def __str__(self):
            return self.value
    monkeypatch.setattr(discovery, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(discovery, "Path", Drive)
    assert discovery._browse_roots() == [{"name": "C:\\", "path": "C:\\"}]


def test_scanner_cli_uses_offline_engine_and_explicit_exclusions(monkeypatch, capsys):
    from scripts import discover_provider_account_directories as command
    calls = []
    monkeypatch.setattr(command.sys, "argv", ["discover", "--provider", "codex", "--excluded", "/excluded/one", "--excluded", "/excluded/two"])
    expected = {"candidates": [], "truncated": False, "warnings": []}
    def invoke(provider, **kwargs):
        calls.append((provider, kwargs))
        return expected
    monkeypatch.setattr(command, "discover", invoke)
    assert command.main() == 0
    assert calls == [("codex", {"excluded": ["/excluded/one", "/excluded/two"]})]
    assert json.loads(capsys.readouterr().out) == expected
