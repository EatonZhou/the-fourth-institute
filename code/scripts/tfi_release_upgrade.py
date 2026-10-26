"""Upgrade/recover operations behind the fixed isolated installation entrypoint.

Only declared exact source versions are accepted. No release seed database or
data directory is imported over a local site. Offline snapshots pair code,
configuration, credentials, five roots and the independent PostgreSQL cluster.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import stat
import time


JOURNAL = "maintenance/upgrade.json"


def tree_inventory(root):
    result = {}
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[name] = {"link": os.readlink(path)}
        elif path.is_file():
            with path.open("rb") as stream:
                result[name] = {"sha256": hashlib.file_digest(stream, "sha256").hexdigest(),
                                "mode": stat.S_IMODE(path.stat().st_mode)}
        elif path.is_dir():
            result[name] = {"directory": True, "mode": stat.S_IMODE(path.stat().st_mode)}
        else:
            raise ValueError(f"Unsupported non-file in upgrade backup: {name}")
    return result


def snapshot(api, root, destination):
    if destination.exists():
        raise ValueError("Upgrade snapshot already exists; recovery must inspect the existing attempt")
    def ignore(directory, names):
        parent = Path(directory)
        excluded = {"maintenance", ".install.lock", ".configuration.lock"} if parent == root else set()
        # Service socket inodes are ephemeral; regular files with .sock names
        # and all local data remain in the snapshot.
        return [name for name in names if name in excluded or stat.S_ISSOCK((parent / name).lstat().st_mode)]
    shutil.copytree(root, destination, symlinks=True, ignore=ignore)
    destination.chmod(0o700)
    inventory = tree_inventory(destination)
    api.write_json(destination.parent / (destination.name + "-inventory.json"), inventory)
    return inventory


def checked_backup(api, root, journal):
    if not re.fullmatch(r"upgrade-[0-9a-f]{16}-[0-9]+", journal["id"]):
        raise ValueError("Invalid upgrade attempt identity")
    if journal["target"]["install_root"] != str(root):
        raise ValueError("Upgrade journal belongs to another installation")
    parent = root / "maintenance" / journal["id"]
    if parent.is_symlink() or not parent.resolve().is_relative_to(root.resolve()):
        raise ValueError("Upgrade backup path escapes the installation")
    backup = parent / "backup"
    if backup.is_symlink() or not backup.is_dir():
        raise ValueError("Upgrade backup is unavailable")
    expected = api.read_json(parent / "backup-inventory.json")
    if tree_inventory(backup) != expected:
        raise ValueError("Upgrade backup integrity check failed; no restore performed")
    original = api.read_json(backup / api.STATE_FILE)
    if original["target"] != journal["target"] or original["manifest_sha256"] != journal["from_manifest_sha256"]:
        raise ValueError("Upgrade backup ownership mismatch")
    return backup


def stage(api, root, journal, phase, **details):
    journal.update(phase=phase, **details)
    journal.setdefault("stages", []).append({"phase": phase, "time": time.time()})
    api.write_json(root / JOURNAL, journal)


def stop_all(api, target):
    api.check_available(target, existing=True)
    control = api.service_controller()
    for component in reversed(api.COMPONENTS):
        control.stop(api.unit_name(target, component))
    states = [control.status(api.unit_name(target, c)) for c in api.COMPONENTS]
    if any(s["pid"] or s["active"] not in {"inactive", "failed"} for s in states):
        raise RuntimeError("Upgrade requires all owned services stopped; snapshot/restore refused")


def check_quiescent(api, target):
    # Run against the target DSN explicitly, never process/global Portal config.
    import psycopg
    with psycopg.connect(api.clean_environment(target)["PORTAL_DATABASE_URL"]) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        counts = [connection.execute(query).fetchone()[0] for query in (
            "SELECT count(*) FROM lineage.conversation_input_states WHERE state IN ('queued','running')",
            "SELECT count(*) FROM lineage.resident_execution_work",
            # Operator checkpoints also own durable pause/cursor settings.
            # Their existence alone is not outstanding business work.
            "SELECT count(*) FROM lineage.operator_work WHERE "
            "COALESCE(checkpoint_json::jsonb->'pending_deliveries','{}'::jsonb)<>'{}'::jsonb",
            "SELECT count(*) FROM lineage.pending_event_deliveries",
            "SELECT count(*) FROM lineage.pending_expiry_notices",
            "SELECT count(*) FROM agent.executions WHERE status IN ('preparing','queued','running')")]
    if any(counts):
        raise RuntimeError("Local site has active/queued work; wait for it to drain before upgrade")


def preflight(api, root, state, manifest_path):
    if state["target"].get("shared_code_root"):
        raise ValueError("Shared-code instances upgrade through their common checkout; portable-bundle replacement is not applicable")
    manifest = api.read_json(manifest_path)
    platform = api.platform_preflight(manifest)
    target = state["target"]
    for name in ("runtime", "code", "config"):
        if (root / name).is_symlink():
            raise ValueError("Installation core directories must not redirect outside their owner")
    for path in (root / "runtime/pg").rglob("*"):
        if path.is_symlink() and not path.resolve().is_relative_to(root):
            raise ValueError("PostgreSQL storage outside this installation cannot be backed up by this upgrade")
    # The site's installation identity remains fixed. The new release keeps
    # its own run identity in version evidence rather than reassigning the site.
    validation_target = {**target, "run_id": manifest.get("run_id"),
                         "supervisor_resident_id": manifest.get("supervisor_resident_id")}
    manifest, bundle = api.validate_engineering(manifest_path, validation_target)
    platform = api.platform_preflight(manifest)
    target_hash = api.digest(manifest_path)
    if state["manifest_sha256"] == target_hash:
        return manifest, bundle, platform, []
    declaration = manifest.get("upgrade", {})
    origins = declaration.get("from_manifest_sha256", [])
    if not isinstance(origins, list) or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in origins):
        raise ValueError("Upgrade origins must be an explicit list of full manifest SHA256 values")
    if state["manifest_sha256"] not in origins:
        raise ValueError("This engineering does not declare the installed manifest as a supported upgrade origin")
    migrations = declaration.get("migrations")
    if not isinstance(migrations, list):
        raise ValueError("Upgrade requires an explicit migrations list (empty means compatible schema)")
    ids = set()
    for item in migrations:
        identifier = item.get("id", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", identifier) or identifier in ids:
            raise ValueError("Migration IDs must be unique stable identifiers")
        ids.add(identifier)
        path = api.engineering_path(bundle, item["path"])
        if path.suffix != ".sql" or api.digest(path) != item["sha256"]:
            raise ValueError("Migration SQL checksum mismatch")
        if not any(row["path"] == item["path"] and row["sha256"] == item["sha256"] for row in manifest["inventory"]):
            raise ValueError("Migration SQL is outside the engineering inventory")
    required_scripts = ["scripts/tfi_release_install.py", "scripts/tfi_release_upgrade.py"]
    if manifest.get("feature_resolution"):
        required_scripts.append("scripts/tfi_release_initialization.py")
    for required in required_scripts:
        if not (api.engineering_path(bundle, manifest["code"]["path"]) / required).is_file():
            raise ValueError("Upgrade engineering is missing its installation/recovery owner")
    platform["dependency_checks"] = api.dependency_preflight(target, bundle, manifest=manifest)
    pg_version = next(row["version"] for row in platform["dependency_checks"] if row["dependency"] == "PostgreSQL")
    major = re.search(r"PostgreSQL\)?\s+(\d+)", pg_version)
    if not major or (root / "runtime/pg/PG_VERSION").read_text().strip() != major.group(1):
        raise ValueError("This upgrade supports the existing PostgreSQL major only")
    platform.update(engineering_sha256=target_hash, checked_at=time.time())
    return manifest, bundle, platform, migrations


def migrate(api, target, bundle, migrations):
    import psycopg
    from psycopg.pq import TransactionStatus
    with psycopg.connect(api.clean_environment(target)["PORTAL_DATABASE_URL"], autocommit=True) as connection:
        with connection.transaction():
            connection.execute("CREATE SCHEMA IF NOT EXISTS tfi_installer")
            connection.execute("CREATE TABLE IF NOT EXISTS tfi_installer.migrations (id text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())")
            for item in migrations:
                row = connection.execute("SELECT sha256 FROM tfi_installer.migrations WHERE id=%s", (item["id"],)).fetchone()
                if row:
                    if row[0] != item["sha256"]:
                        raise ValueError("An applied migration ID has a different checksum")
                    continue
                sql = api.engineering_path(bundle, item["path"]).read_text(encoding="utf-8")
                connection.execute(sql, prepare=False)
                if connection.info.transaction_status != TransactionStatus.INTRANS:
                    raise ValueError("Migration ended its transaction; offline snapshot recovery required")
                connection.execute("INSERT INTO tfi_installer.migrations(id,sha256) VALUES (%s,%s)", (item["id"], item["sha256"]))


def start_and_check(api, state):
    target = state["target"]
    control = api.service_controller()
    for component in api.COMPONENTS:
        control.start(api.unit_name(target, component))
        if component == "postgres":
            api.wait_database(target)
    deadline = time.monotonic() + 360
    while True:
        result = api.observed_status(state)
        if result["status"] == "installed":
            return result
        if time.monotonic() >= deadline:
            raise RuntimeError("Upgraded services failed health checks")
        time.sleep(2)


def restore(api, root, journal, *, retain_failed):
    backup = checked_backup(api, root, journal)
    stop_all(api, journal["target"])
    if retain_failed:
        failed = backup.parent / ("failed-" + str(time.time_ns()))
        snapshot(api, root, failed)
        journal["failed_generation_backup"] = str(failed)
    stage(api, root, journal, "restoring")
    for child in root.iterdir():
        if child.name in {"maintenance", ".install.lock", ".configuration.lock", api.STATE_FILE}:
            continue
        if child.name == "config":
            # Deployment identity now lives here, not in installation.json.
            # Keep its authority readable throughout a retryable restore.
            for local in child.iterdir():
                if local.name == "target.json":
                    continue
                if local.is_dir() and not local.is_symlink():
                    shutil.rmtree(local)
                else:
                    local.unlink()
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    for child in backup.iterdir():
        if child.name == api.STATE_FILE:
            continue
        destination = root / child.name
        if child.is_symlink():
            destination.symlink_to(os.readlink(child))
        elif child.is_dir():
            shutil.copytree(child, destination, symlinks=True, dirs_exist_ok=child.name == "config")
        else:
            shutil.copy2(child, destination)
    # Keep a valid ownership record throughout a crash-interrupted restore.
    api.write_json(root / api.STATE_FILE, api.read_json(backup / api.STATE_FILE))
    stage(api, root, journal, "restored_stopped")
    return {"status": "restored_stopped", "installation_root": str(root),
            "manifest_sha256": journal["from_manifest_sha256"],
            "failed_generation_backup": journal.get("failed_generation_backup"),
            "next_step": "Inspect the restored site, then resume with its original engineering and the fixed install command"}


def run(api, command, root, manifest_path=None):
    root = Path(root).resolve()
    state = api.owned_state(root)
    target = state["target"]
    if (root / "maintenance").is_symlink():
        raise ValueError("Installation maintenance directory must not be a symlink")
    journal_path = root / JOURNAL
    previous = api.read_json(journal_path) if journal_path.is_file() else None
    if previous and previous["target"] != target:
        raise ValueError("Upgrade journal target does not match the owned installation")
    if command == "recover":
        if not previous or previous["phase"] in {"complete", "restored_stopped"}:
            raise ValueError("No interrupted upgrade requires recovery")
        with api.installation_lock(root):
            if previous["phase"] in {"quiescing", "backing_up", "prebackup_failed"}:
                if state["manifest_sha256"] != previous["from_manifest_sha256"]:
                    raise ValueError("Prebackup recovery found a changed installation version")
                stop_all(api, target)
                stage(api, root, previous, "restored_stopped")
                return {"status": "restored_stopped", "installation_root": str(root),
                        "reason": "Backup was incomplete; code/database were not changed. Resume using the original engineering."}
            return restore(api, root, previous, retain_failed=True)
    manifest, bundle, platform, migrations = preflight(api, root, state, manifest_path)
    target_hash = api.digest(manifest_path)
    if previous and previous["phase"] not in {"complete", "restored_stopped", "prebackup_failed"}:
        raise ValueError("Interrupted upgrade found; run recover before retrying (no migrations replayed)")
    if state["manifest_sha256"] == target_hash:
        result = api.observed_status(state)
        result["upgrade"] = "already_applied"
        return result
    if state.get("status") != "installed" or api.observed_status(state)["status"] != "installed":
        raise ValueError("Upgrade requires a previously installed healthy independent site")
    check_quiescent(api, target)
    journal = {"id": "upgrade-" + target_hash[:16] + "-" + str(time.time_ns()),
               "target": target, "from_manifest_sha256": state["manifest_sha256"],
               "to_manifest_sha256": target_hash, "platform_preflight": platform,
               "migration_ids": [item["id"] for item in migrations]}
    with api.installation_lock(root):
        # The source state must not have changed while waiting for the lock.
        if api.read_json(root / api.STATE_FILE) != state:
            raise ValueError("Installation changed during upgrade preflight")
        (root / "maintenance").mkdir(mode=0o700, exist_ok=True)
        parent = root / "maintenance" / journal["id"]
        parent.mkdir(mode=0o700)
        stage(api, root, journal, "quiescing")
        try:
            control = api.service_controller()
            control.stop(api.unit_name(target, "web"))
            control.stop(api.unit_name(target, "society"))
            check_quiescent(api, target)
            stop_all(api, target)
            stage(api, root, journal, "backing_up")
            snapshot(api, root, parent / "backup")
            stage(api, root, journal, "backup_ready")
        except Exception as exc:
            stage(api, root, journal, "prebackup_failed", error=type(exc).__name__ + ": " + str(exc))
            # No code/data changed; restore original service availability.
            start_and_check(api, state)
            raise RuntimeError("Upgrade preparation failed; inspect the private maintenance journal") from exc
        try:
            stage(api, root, journal, "migrating")
            control.start(api.unit_name(target, "postgres"))
            api.wait_database(target)
            migrate(api, target, bundle, migrations)
            stop_all(api, target)
            stage(api, root, journal, "switching")
            old_manifest = api.read_json(root / "runtime/engineering-manifest.json") if (root / "runtime/engineering-manifest.json").is_file() else {}
            old_features = old_manifest.get("feature_resolution", {}).get("included_features", [])
            shutil.copytree(api.engineering_path(bundle, manifest["code"]["path"]), parent / "new-code")
            (root / "code").rename(parent / "previous-code")
            (parent / "new-code").rename(root / "code")
            shutil.copyfile(root / "code/scripts/tfi_release_install.py", root / "runtime/installer.py")
            shutil.copyfile(manifest_path, root / "runtime/engineering-manifest.json")
            state.update(manifest_sha256=target_hash, platform_preflight=platform,
                         version_run_id=manifest["run_id"], upgrade_attempt=journal["id"])
            api.save_stage(state, "upgrade_switch", "upgrading")
            stage(api, root, journal, 'dependencies')
            api.prepare_dependencies(state, manifest)
            if manifest.get("feature_resolution"):
                stage(api, root, journal, "feature_initialization")
                control.start(api.unit_name(target, "postgres"))
                api.wait_database(target)
                check_quiescent(api, target)
                api.initialize_features(state, manifest, mode="upgrade", existing_feature_ids=old_features)
        except Exception as exc:
            stage(api, root, journal, "migration_or_switch_failed", error=type(exc).__name__ + ": " + str(exc))
            try:
                restore(api, root, journal, retain_failed=True)
            except Exception as recovery_error:
                stage(api, root, journal, "recovery_required", recovery_error=str(recovery_error))
            raise RuntimeError("Upgrade migration/switch failed; inspect the private maintenance journal and recovery state") from exc
        stage(api, root, journal, "starting")
        try:
            result = start_and_check(api, state)
        except Exception as exc:
            stop_all(api, target)
            # Starting the new site may have accepted local writes. Keep its
            # paired code+DB intact; explicit recovery first saves this state.
            stage(api, root, journal, "recovery_required", error=str(exc))
            raise RuntimeError("Upgrade health failed; site stopped with matching code/database. Retry recover to preserve the failed generation and restore the complete backup") from exc
        api.save_stage(state, "upgrade_health", "installed")
        stage(api, root, journal, "complete")
        result.update(upgrade="applied", from_manifest_sha256=journal["from_manifest_sha256"],
                      manifest_sha256=target_hash, backup=str(parent / "backup"),
                      feature_initialization=state.get("feature_initialization", {}).get("result", {"status": "legacy_not_declared"}),
                      functional_review="pending_resident")
        api.write_json(root / "installation-result.json", result)
        return result
