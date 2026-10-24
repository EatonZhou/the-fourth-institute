"""First registration may explicitly reuse this OS user's native login.

:module: execution.providers
"""
import json
from pathlib import Path

import pytest

from management_portal.execution import account_hosting, account_registrations


def test_registration_accepts_explicit_local_login_but_not_api_or_untyped_value():
    for provider in ("codex", "claude"):
        row = dict(account_id="first", provider=provider, kind="subscription_login",
                   config={"reuse_local_login": True})
        account_registrations._validate(row)
        for bad in ("yes", 1, None):
            row["config"]["reuse_local_login"] = bad
            with pytest.raises(Exception):
                account_registrations._validate(row)
        row.update(kind="api_profile", config={"api_id": "api", "reuse_local_login": True})
        with pytest.raises(Exception):
            account_registrations._validate(row)


@pytest.fixture
def local(tmp_path, monkeypatch):
    from management_portal.execution import account_local_login as local
    home = tmp_path / "user"
    home.mkdir()
    monkeypatch.setattr(local, "user_home", lambda: home)
    return local, home


def codex_login(home):
    root = home / ".codex"
    root.mkdir()
    auth = {"auth_mode": "chatgpt", "tokens": {"access_token": "synthetic-access",
            "refresh_token": "synthetic-refresh", "id_token": "synthetic-id", "account_id": "synthetic-account"}}
    (root / "auth.json").write_text(json.dumps(auth))
    (root / "config.toml").write_text('model = "user-model"\nmodel_reasoning_effort = "high"\n[projects."/private"]\ntrust_level = "trusted"\n')
    (root / "history.jsonl").write_text("private history")
    return root, auth


def test_codex_import_copies_only_login_and_model_preserving_source(local, tmp_path):
    local, home = local
    source, auth = codex_login(home)
    originals = {p.name: p.read_bytes() for p in source.iterdir()}
    assert local.describe("codex") == {"available": True, "default_model": "user-model"}
    destination = tmp_path / "account"
    destination.mkdir()
    local.import_login("codex", destination)
    assert json.loads((destination / "auth.json").read_text()) == auth
    assert (destination / "auth.json").stat().st_mode & 0o777 == 0o600
    assert set(p.name for p in destination.iterdir()) == {"auth.json", "config.toml"}
    assert "projects" not in (destination / "config.toml").read_text()
    assert {p.name: p.read_bytes() for p in source.iterdir()} == originals
    (destination / "auth.json").write_text("new-login")
    with pytest.raises(Exception):
        local.import_login("codex", destination)
    assert (destination / "auth.json").read_text() == "new-login"


def test_claude_empty_tokens_are_not_available_and_api_configuration_is_not_subscription(local, tmp_path):
    local, home = local
    root = home / ".claude"
    root.mkdir()
    credentials = root / ".credentials.json"
    credentials.write_text(json.dumps({"claudeAiOauth": {"accessToken": "", "refreshToken": ""}}))
    assert local.describe("claude")["available"] is False
    credentials.write_text(json.dumps({"claudeAiOauth": {"accessToken": "synthetic-access", "refreshToken": "synthetic-refresh"}}))
    (root / "settings.json").write_text(json.dumps({"model": "user-claude", "env": {"ANTHROPIC_BASE_URL": "https://example.invalid"}}))
    assert local.describe("claude")["available"] is False
    (root / "settings.json").write_text(json.dumps({"model": "user-claude"}))
    assert local.describe("claude") == {"available": True, "default_model": "user-claude"}
    destination = tmp_path / "account"
    destination.mkdir()
    local.import_login("claude", destination)
    assert json.loads((destination / "settings.json").read_text()) == {"model": "user-claude"}
    assert (destination / ".credentials.json").exists()


def test_missing_or_symlink_source_rejected_without_partial_copy(local, tmp_path):
    local, home = local
    root, auth = codex_login(home)
    destination = tmp_path / "account"
    destination.mkdir()
    (root / "auth.json").unlink()
    assert local.describe("codex")["available"] is False
    target = tmp_path / "foreign"
    target.write_text(json.dumps(auth))
    (root / "auth.json").symlink_to(target)
    with pytest.raises(Exception):
        local.import_login("codex", destination)
    assert not list(destination.iterdir())


def test_registration_rollback_removes_only_imported_files(local, tmp_path, monkeypatch):
    local, home = local
    source, _ = codex_login(home)
    original = (source / "auth.json").read_bytes()
    target = tmp_path / "accounts" / "first"
    target.parent.mkdir()
    monkeypatch.setattr(account_hosting, "account_home", lambda *_: target)
    with pytest.raises(RuntimeError, match="commit failed"):
        with local.registration_home("codex", "first"):
            assert (target / "auth.json").is_file()
            raise RuntimeError("commit failed")
    assert not target.exists()
    assert (source / "auth.json").read_bytes() == original


def test_second_file_failure_rolls_back_complete_import(local, tmp_path, monkeypatch):
    local, home = local
    codex_login(home)
    target = tmp_path / "account"
    target.mkdir()
    write = local.atomic_write_private_bytes
    def fail_config(path, content):
        if path.name == "config.toml":
            raise OSError("synthetic filesystem failure")
        write(path, content)
    monkeypatch.setattr(local, "atomic_write_private_bytes", fail_config)
    with pytest.raises(Exception):
        local.import_login("codex", target)
    assert not list(target.iterdir())


def test_os_identity_discovery_does_not_borrow_agent_account(monkeypatch):
    import os
    import pwd
    from management_portal.execution import account_local_login as local
    monkeypatch.setenv("HOME", "/foreign-instance/home")
    monkeypatch.setenv("CODEX_HOME", "/another-resident/account")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/another-resident/claude")
    assert local.user_home() == Path(pwd.getpwuid(os.getuid()).pw_dir)


@pytest.mark.parametrize('failure', ['insert', 'commit'])
def test_failed_registration_keeps_identity_exclusive_until_file_rollback(local, tmp_path, monkeypatch, failure):
    from contextlib import contextmanager
    from management_portal.hatg.contracts import InstructionError
    local, home = local
    codex_login(home)
    target = tmp_path / 'accounts' / 'first'
    target.parent.mkdir()
    monkeypatch.setattr(account_hosting, 'account_home', lambda *_: target)
    base = dict(account_id='first', provider='codex', kind='subscription_login')
    events = []

    class Result:
        def __init__(self, value): self.value = value
        def fetchone(self): return self.value

    class Database:
        first = True
        @contextmanager
        def transaction(self, key):
            try:
                yield self
                if self.first and failure == 'commit':
                    raise RuntimeError('synthetic commit failure')
            except RuntimeError:
                # PG has rolled back/released its transaction lock here.
                self.first = False
                try:
                    account_registrations.create_registration(base, 'second')
                except InstructionError:
                    events.append('second rejected until file rollback')
                else:
                    events.append('second committed too early')
                    target.joinpath('auth.json').write_text('second login')
                raise
        def execute(self, sql, args):
            if 'INSERT INTO registrations' not in sql:
                return Result(None)
            if self.first and failure == 'insert':
                raise RuntimeError('synthetic insert failure')
            return Result({**base, 'config': {}, 'enabled': True})

    db = Database()
    monkeypatch.setattr(account_registrations, 'database', lambda: db)
    with pytest.raises(RuntimeError, match='synthetic'):
        account_registrations.create_registration({**base, 'config': {'reuse_local_login': True}}, 'first')
    assert events == ['second rejected until file rollback']
    assert not target.exists()
    assert account_registrations.create_registration(base, 'second') is not None
    account_hosting.ensure_account_home('codex', 'first')
    target.joinpath('auth.json').write_text('second login')
    assert target.joinpath('auth.json').read_text() == 'second login'


@pytest.mark.parametrize('provider', ['codex', 'claude'])
def test_import_ignores_unrelated_credential_fields(local, tmp_path, provider):
    local, home = local
    if provider == 'codex':
        root, auth = codex_login(home)
        name, key = 'auth.json', 'tokens'
    else:
        root = home / '.claude'
        root.mkdir()
        name, key = '.credentials.json', 'claudeAiOauth'
        auth = {key: {'accessToken': 'synthetic', 'refreshToken': 'synthetic'}}
    auth['unrelated_credential'] = 'private'
    auth[key]['unrelated_credential'] = 'private'
    root.joinpath(name).write_text(json.dumps(auth))
    destination = tmp_path / 'target'
    destination.mkdir()
    local.import_login(provider, destination)
    imported = json.loads(destination.joinpath(name).read_text())
    assert 'unrelated_credential' not in imported
    assert 'unrelated_credential' not in imported[key]
    assert json.loads(root.joinpath(name).read_text()) == auth
