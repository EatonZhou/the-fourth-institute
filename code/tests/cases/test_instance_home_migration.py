"""Merging an old-layout instance HOME into the OS user's home (Plan step 5)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts import instance_storage_migration as common
from scripts import migrate_instance_home as home_migration
from tests.cases.instance_test_support import write_instance


def _write(path: Path, text: str = "x", mode: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mode is not None:
        path.chmod(mode)
    return path


@pytest.fixture
def layout(tmp_path, monkeypatch):
    root = write_instance(tmp_path / "instance")
    instance = home_migration.instance_for(type("Args", (), {"root": root})())
    old, home = root / "home", tmp_path / "user-home"
    (instance.directory("secrets")).mkdir(parents=True, exist_ok=True)
    home.mkdir()
    monkeypatch.setattr(home_migration, "processes_using", lambda prefixes, ignore=(): [])
    monkeypatch.setattr(home_migration, "database_rows", lambda instance, queries: None)
    return instance, old, home


def _plan(instance, home, **options):
    return home_migration.build_plan(instance, home=home, files=options.pop("files", []), **options)[0]


def test_only_old_entries_move_caches_keep_home_and_configs_are_not_overwritten(layout):
    instance, old, home = layout
    _write(old / ".keras/keras.json", "{}")
    _write(old / ".cache/pip/http/a", "old-a")
    _write(old / ".cache/pip/http/shared", "old copy")
    _write(home / ".cache/pip/http/shared", "home copy")
    _write(old / ".gitconfig", "[user]\n name = old\n")
    _write(home / ".gitconfig", "[user]\n name = home\n")
    _write(old / ".profile", "same\n")
    _write(home / ".profile", "same\n")

    plan = _plan(instance, home)
    kinds = {Path(move.source).relative_to(old).as_posix(): move.kind for move in plan.moves}
    assert kinds == {".keras": "only-old-home", ".cache/pip/http/a": "cache"}
    assert {item["reason"] for item in plan.discards} == {"cache file kept from the home", "identical to the home's copy"}
    assert [item["relative"] for item in plan.conflicts] == [".gitconfig"]
    assert any(line.startswith("+ name = old") for line in plan.configs[0]["diff"])

    home_migration.apply(instance, home=home, check_services=False)
    assert (home / ".keras/keras.json").read_text() == "{}"
    assert (home / ".cache/pip/http/a").read_text() == "old-a"
    assert (home / ".cache/pip/http/shared").read_text() == "home copy"
    assert (home / ".gitconfig").read_text().endswith("name = home\n")
    assert (old / ".gitconfig").exists() and not (old / ".keras").exists() and not (old / ".profile").exists()

    # Running again changes nothing: only the listed conflict is left.
    again = _plan(instance, home)
    assert again.moves == [] and again.discards == [] and [c["relative"] for c in again.conflicts] == [".gitconfig"]


def test_ssh_keys_identical_discarded_different_listed_new_moved_and_authorized_keys_only_compared(layout):
    instance, old, home = layout
    _write(old / ".ssh/id_same", "KEY", 0o600)
    _write(home / ".ssh/id_same", "KEY", 0o600)
    _write(old / ".ssh/id_other", "OLD KEY", 0o600)
    _write(home / ".ssh/id_other", "HOME KEY", 0o600)
    _write(old / ".ssh/id_new", "NEW KEY", 0o600)
    _write(old / ".ssh/authorized_keys", "ssh-ed25519 AAA one\nssh-ed25519 BBB two\n")
    _write(home / ".ssh/authorized_keys", "ssh-ed25519 AAA one\n")

    plan = _plan(instance, home)
    assert plan.ssh["moved"] == [".ssh/id_new"]
    assert plan.ssh["discarded_identical"] == [".ssh/id_same"]
    assert plan.ssh["conflicts"] == [".ssh/id_other"]
    assert len(plan.ssh["authorized_keys"]["only_in_old_home_sha256"]) == 1
    assert "OLD KEY" not in json.dumps(plan.conflicts) + json.dumps(plan.ssh)

    home_migration.apply(instance, home=home, check_services=False)
    assert (home / ".ssh/id_new").stat().st_mode & 0o777 == 0o600
    assert (home / ".ssh/id_other").read_text() == "HOME KEY"
    assert (old / ".ssh/id_other").exists() and (old / ".ssh/authorized_keys").exists()
    assert not (old / ".ssh/id_same").exists()


def test_apply_refuses_while_a_process_uses_the_old_home(layout, monkeypatch):
    instance, old, home = layout
    _write(old / ".codex/auth.json", "{}")
    _write(old / ".keras/keras.json", "{}")
    users = [{"pid": 4242, "command": "codex", "use": "fd", "path": str(old / ".codex/auth.json")}]
    monkeypatch.setattr(home_migration, "processes_using", lambda prefixes, ignore=(): users)

    plan = _plan(instance, home)
    assert plan.codex["processes"] == users and "in use" in plan.codex["decision"]
    with pytest.raises(RuntimeError, match="Processes still use the old HOME"):
        home_migration.apply(instance, home=home, check_services=False)
    assert (old / ".codex/auth.json").exists() and (old / ".keras").exists()


def test_unregistered_codex_copy_is_deleted_when_unused_and_never_merged(layout):
    instance, old, home = layout
    _write(old / ".codex/auth.json", "{}")
    _write(home / ".codex/config.toml", "")
    report = home_migration.apply(instance, home=home, check_services=False).document()
    assert report["codex_deleted"] is True
    assert not (old / ".codex").exists() and not (home / ".codex/auth.json").exists()


def test_failed_rename_reverses_the_renames_already_made(layout, monkeypatch):
    instance, old, home = layout
    _write(old / ".aaa/file", "a")
    _write(old / ".bbb/file", "b")
    real = os.rename

    def failing(source, target):
        if str(source).endswith(".bbb"):
            raise OSError("simulated")
        return real(source, target)

    monkeypatch.setattr(common.os, "rename", failing)
    with pytest.raises(OSError, match="simulated"):
        home_migration.apply(instance, home=home, check_services=False)
    assert (old / ".aaa/file").exists() and not (home / ".aaa").exists()


def test_instance_data_secret_copies_avatars_and_obsolete_leftovers(layout, monkeypatch):
    instance, old, home = layout
    secrets = instance.directory("secrets")
    _write(secrets / "session_signing.key", "SIGN")
    _write(secrets / "service.key", "S")
    _write(old / ".dirtybase/secrets/service.key", "S")
    _write(old / ".dirtybase/secrets/retired.key", "R")
    _write(home / ".dirtybase/portal/session_signing.key", "SIGN")
    _write(old / ".config/tfi/portal_paths.json", "{}")
    _write(old / ".tfi/portal/resident_avatars/resident_000000000001.json",
           json.dumps({"resident_id": "resident_000000000001", "sprite_index": 1}))

    kept = _plan(instance, home)
    assert kept.avatars["verified"] is False
    assert not any("avatar" in item["reason"] for item in kept.discards)

    monkeypatch.setattr(home_migration, "database_rows",
                        lambda instance, queries: {"avatars": [("resident", "resident_000000000001")]})
    home_migration.apply(instance, home=home, check_services=False)
    assert not (old / ".dirtybase/secrets/service.key").exists()
    assert not (old / ".dirtybase/secrets/retired.key").exists()
    assert (secrets / "retired.key").read_text() == "R"
    assert not (home / ".dirtybase/portal/session_signing.key").exists()
    assert not (old / ".config/tfi/portal_paths.json").exists()
    assert not (old / ".tfi/portal/resident_avatars").exists()
    assert (secrets / "session_signing.key").read_text() == "SIGN"


def test_hardcoded_old_home_paths_rewritten_only_where_they_still_resolve(layout, tmp_path, monkeypatch):
    instance, old, home = layout
    _write(old / ".cache/huggingface/hub/model", "m")
    note = _write(tmp_path / "lineage/notes.md",
                  f"cache {old}/.cache/huggingface/hub\npackage {old}/.local/share/removed-package\n")
    plan = _plan(instance, home, files=[note])
    assert [entry["path"] for entry in plan.hardcoded["files_to_rewrite"]] == [str(note)]
    assert plan.hardcoded["unresolved_references"][0]["unresolved"] == [f"{old}/.local/share/removed-package"]

    # Apply plans again from the live state; give it the same candidate files.
    monkeypatch.setattr(home_migration, "hardcoded_candidates", lambda instance, home: [note])
    home_migration.apply(instance, home=home, check_services=False)
    text = note.read_text()
    assert f"cache {home}/.cache/huggingface/hub" in text
    assert f"package {old}/.local/share/removed-package" in text
    assert (home / ".cache/huggingface/hub/model").exists()


def test_unit_files_are_never_installed_by_moving(layout):
    instance, old, home = layout
    _write(old / ".config/systemd/user/example.service", "[Service]\nExecStart=/bin/true\n")
    (home / ".config/systemd/user").mkdir(parents=True)
    plan = _plan(instance, home)
    assert plan.moves == []
    assert plan.report_only[0]["relative"] == ".config/systemd/user/example.service"


def test_aliases_map_the_old_home_for_the_database_conversion(layout):
    instance, old, home = layout
    plan, users = home_migration.build_plan(instance, home=home, files=[])
    document = home_migration.report_for(plan, users, (), apply=False, root=instance.root).document()
    assert document["aliases"] == [{"source": str(old), "target": str(home), "kind": "home"}]
    assert document["mode"] == "dry-run"
