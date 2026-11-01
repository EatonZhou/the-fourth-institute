"""One-time staging split: known directories go where the code reads them, nothing else moves."""
import json
from pathlib import Path

import pytest

from management_common.instance_manifest import validate
from scripts import instance_storage_migration, migrate_instance_staging as staging_migration
from tests.cases.instance_test_support import write_instance


def _instance(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "instance"
    fake = tmp_path / "pg"
    write_instance(root, postgres={"bin": fake / "bin", "lib": fake / "lib", "share": fake / "share"})
    staging = root / "data-roots" / "staging"
    document = json.loads((root / "instance.json").read_text())
    document["migration"] = {"staging_root": str(staging)}
    (root / "instance.json").write_text(json.dumps(document))
    return root, staging


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _layout(root: Path, staging: Path) -> None:
    _write(staging / "shared/portal_paper_workspace/paper.json")
    _write(staging / "shared/kimi-code-portal/workspaces.json",
           json.dumps({"home": str(staging / "shared/kimi-code-portal/sessions/a")}))
    _write(staging / "shared/portal_uploads/resident_chat_images/a.png")
    _write(staging / "portal_locks/codex-app-server-threads/t.lock")
    _write(staging / "portal_locks/old.lock")
    _write(staging / "portal_execution/activity/e.json")
    _write(staging / "shared/robolab/run.log")
    _write(staging / "fork-v38.log")
    (staging / "portal_instances").symlink_to("../instances")
    _write(staging.parent / "instances/uploads/upload_1/file.bin")
    _write(staging.parent / "instances/settings/resident_embeddings.json")


@pytest.fixture
def offline(monkeypatch):
    calls = []
    monkeypatch.setattr(staging_migration, "require_offline", lambda instance, running=(): calls.append(running) or {})
    monkeypatch.setattr(staging_migration, "require_protected", lambda pids: {})
    monkeypatch.setattr(staging_migration, "processes_using", lambda paths, ignore=(): [])
    import management_portal.provider_session_relocation as relocation
    monkeypatch.setattr(relocation, "relocate_provider_sessions",
                        lambda old, new, apply=True: calls.append(("sessions", str(old), str(new))) or {"changed": []})
    return calls


def test_plan_maps_known_directories_and_lists_the_rest(tmp_path):
    root, staging = _instance(tmp_path)
    _layout(root, staging)
    instance, _document, selected = staging_migration.read_instance(root)
    assert selected == staging
    result = staging_migration.plan(instance, staging, historical_roots=(tmp_path / "old-staging",), owners=False)
    targets = {Path(move.source).relative_to(root).as_posix(): Path(move.target).relative_to(root).as_posix()
               for move in result["moves"]}
    assert targets["data-roots/staging/shared/portal_paper_workspace"] == "data/features/paper_workspace"
    assert targets["data-roots/staging/shared/kimi-code-portal"] == "data/provider-state/kimi-code"
    assert targets["data-roots/staging/portal_execution"] == "data/features/execution"
    assert targets["data-roots/staging/portal_locks/codex-app-server-threads"] == "runtime/codex-app-server-threads"
    assert targets["data-roots/staging/portal_locks/old.lock"] == "runtime/old.lock"
    assert targets["data-roots/instances/settings"] == "runtime/settings"
    # Two old upload directories fill one area, entry by entry.
    assert targets["data-roots/staging/shared/portal_uploads/resident_chat_images"] == "data/uploads/resident_chat_images"
    assert targets["data-roots/instances/uploads/upload_1"] == "data/uploads/upload_1"
    assert [item["name"] for item in result["scattered"]] == ["fork-v38.log"]
    assert [item["relative"] for item in result["unmapped_shared"]] == ["shared/robolab"]
    assert result["obsolete_links"][0]["path"] == str(staging / "portal_instances")
    aliases = {(alias.source, alias.kind): alias.target for alias in result["aliases"]}
    assert aliases[(str(staging / "portal_instances/uploads"), "old-link")] == str(root / "data/uploads")
    assert aliases[(str(tmp_path / "old-staging/shared/portal_paper_workspace"), "historical-root")] == \
        str(root / "data/features/paper_workspace")


def test_apply_moves_rewrites_provider_state_and_drops_the_pending_manifest_entry(tmp_path, offline):
    root, staging = _instance(tmp_path)
    _layout(root, staging)
    report = tmp_path / "report.json"
    assert staging_migration.main(["--root", str(root), "--apply", "--report", str(report)]) == 0
    assert (root / "data/features/paper_workspace/paper.json").is_file()
    assert (root / "data/uploads/upload_1/file.bin").is_file()
    assert (root / "data/uploads/resident_chat_images/a.png").is_file()
    assert (root / "runtime/settings/resident_embeddings.json").is_file()
    kimi = json.loads((root / "data/provider-state/kimi-code/workspaces.json").read_text())
    assert kimi["home"] == str(root / "data/provider-state/kimi-code/sessions/a")
    assert ("sessions", str(staging / "shared/kimi-code-portal"), str(root / "data/provider-state/kimi-code")) in offline
    assert ("postgres",) in offline
    # Loose files and unread directories stay for their owners.
    assert (staging / "fork-v38.log").is_file() and (staging / "shared/robolab/run.log").is_file()
    assert not (staging / "portal_locks").exists() and not (staging / "shared/portal_uploads").exists()
    document = json.loads((root / "instance.json").read_text())
    assert "migration" not in document
    validate(root, document)
    # A rerun names the old root explicitly and finds nothing left to move.
    again = tmp_path / "again.json"
    assert staging_migration.main(["--root", str(root), "--staging-root", str(staging), "--report", str(again)]) == 0
    assert json.loads(again.read_text())["moves"] == []


def test_existing_targets_merge_and_collisions_stay(tmp_path, offline):
    root, staging = _instance(tmp_path)
    _write(staging / "shared/script_runs/a.json", "old")
    _write(staging / "shared/script_runs/b.json", "old")
    _write(root / "runtime/script_runs/a.json", "new")
    report = tmp_path / "report.json"
    assert staging_migration.main(["--root", str(root), "--apply", "--report", str(report)]) == 0
    result = json.loads(report.read_text())
    assert (root / "runtime/script_runs/b.json").read_text() == "old"
    assert (root / "runtime/script_runs/a.json").read_text() == "new"
    assert (staging / "shared/script_runs/a.json").read_text() == "old"
    assert result["conflicts"] == [{"source": str(staging / "shared/script_runs/a.json"),
                                    "target": str(root / "runtime/script_runs/a.json"), "kind": "staging-split"}]


def test_a_failed_rename_reverses_the_command_and_keeps_the_manifest(tmp_path, offline, monkeypatch):
    root, staging = _instance(tmp_path)
    _write(staging / "shared/portal_paper_workspace/paper.json")
    _write(staging / "shared/dsh/state.json")
    real_rename = instance_storage_migration.os.rename

    def rename(source, target):
        if str(source).endswith("shared/dsh"):
            raise OSError("disk says no")
        real_rename(source, target)

    monkeypatch.setattr(instance_storage_migration.os, "rename", rename)
    with pytest.raises(OSError):
        staging_migration.main(["--root", str(root), "--apply"])
    assert (staging / "shared/portal_paper_workspace/paper.json").is_file()
    assert not (root / "data/features/paper_workspace").exists()
    assert "migration" in json.loads((root / "instance.json").read_text())


def test_apply_refuses_while_another_process_uses_the_staging_root(tmp_path, offline, monkeypatch):
    root, staging = _instance(tmp_path)
    _write(staging / "shared/dsh/state.json")
    monkeypatch.setattr(staging_migration, "processes_using",
                        lambda paths, ignore=(): [{"pid": 4242, "command": "worker", "use": "cwd", "path": str(staging)}])
    with pytest.raises(RuntimeError, match="Processes still use"):
        staging_migration.main(["--root", str(root), "--apply"])
    assert (staging / "shared/dsh/state.json").is_file()
