"""Keep native Codex rollout indexes relative to the configured account home.

Codex resolves these paths against its process cwd. Service hosts must run it
in the account home; thread cwd is a separate per-request setting. SQLite
triggers normalize native inserts and resume/archive updates at their writer.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import sqlite3

AREAS = ("sessions", "archived_sessions", "legacy-thread-history")


def _relative(path: str, home: Path) -> str | None:
    value = path.replace("\\", "/")
    prefix = home.as_posix().rstrip("/") + "/"
    if value.startswith(prefix):
        value = value[len(prefix):]
    elif value.startswith("/") or (len(value) > 1 and value[1] == ":"):
        # Older native indexes can retain a previous deployment root. Rebase
        # only an existing account-local rollout whose identity is verified.
        matches = [value.split("/" + area + "/", 1)[1] for area in AREAS if "/" + area + "/" in value]
        areas = [area for area in AREAS if "/" + area + "/" in value]
        if len(matches) != 1:
            return None
        value = areas[0] + "/" + matches[0]
    parts = PurePosixPath(value).parts
    if not parts or parts[0] not in AREAS or ".." in parts or "\x00" in value:
        return None
    return PurePosixPath(value).as_posix()


def _trigger_sql(home: Path, event: str) -> str:
    prefix = (home.as_posix().rstrip("/") + "/").replace("'", "''")
    normalized = "replace(NEW.rollout_path, char(92), '/')"
    suffix = f"substr({normalized}, {len(home.as_posix().rstrip('/')) + 2})"
    allowed = " OR ".join(f"{suffix} LIKE '{area}/%'" for area in AREAS)
    return (f"CREATE TRIGGER portal_rollout_relative_{event} AFTER "
            f"{'INSERT' if event == 'insert' else 'UPDATE OF rollout_path'} ON threads "
            f"WHEN substr({normalized}, 1, {len(home.as_posix().rstrip('/')) + 1}) = '{prefix}' "
            f"AND ({allowed}) BEGIN UPDATE threads SET rollout_path={suffix} WHERE id=NEW.id; END")


def prepare_account_index(home: Path | str) -> dict:
    """Migrate existing rows and install atomic writer normalization.

    Only native Codex SQLite files are touched. Missing source files are
    reported, never manufactured or imported from another account.
    """
    home = Path(home).absolute()
    result = {"home": str(home), "databases": []}
    for database in sorted(home.glob("state_*.sqlite")):
        connection = sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=30)
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)")}
            if not {"id", "rollout_path"} <= columns:
                raise ValueError("Unsupported Codex threads index schema")
            updates, unresolved = [], []
            for thread, old in connection.execute("SELECT id, rollout_path FROM threads"):
                relative = _relative(old, home)
                if relative == old:
                    continue
                candidate = home / relative if relative else None
                if candidate is None or not candidate.is_file() or thread not in candidate.name:
                    unresolved.append(thread)
                    continue
                try:
                    with candidate.open("rb") as stream:
                        line = stream.readline(2_000_001)
                    metadata = json.loads(line) if len(line) <= 2_000_000 else {}
                except (OSError, UnicodeError, ValueError):
                    metadata = {}
                payload = metadata.get("payload") if isinstance(metadata, dict) else None
                if not isinstance(payload, dict) or metadata.get("type") != "session_meta" or payload.get("id") != thread:
                    unresolved.append(thread)
                    continue
                updates.append((relative, thread, old))
            backup_path = None
            if updates:
                folder = home / "portal-index-backups"
                folder.mkdir(mode=0o700, exist_ok=True)
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                backup_path = folder / f"{database.stem}-{stamp}.sqlite"
                with sqlite3.connect(backup_path) as backup:
                    connection.backup(backup, pages=256)
                backup_path.chmod(0o600)
            changed = 0
            connection.execute("BEGIN IMMEDIATE")
            for event in ("insert", "update"):
                expected = _trigger_sql(home, event)
                name = f"portal_rollout_relative_{event}"
                actual = connection.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (name,)).fetchone()
                if not actual or actual[0] != expected:
                    connection.execute(f"DROP TRIGGER IF EXISTS {name}")
                    connection.execute(expected)
            for relative, thread, old in updates:
                changed += connection.execute("UPDATE threads SET rollout_path=? WHERE id=? AND rollout_path=?", (relative, thread, old)).rowcount
            connection.commit()
            result["databases"].append({"database": str(database), "updated": changed,
                                         "unresolved": unresolved, "backup": str(backup_path) if backup_path else None})
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", required=True)
    args = parser.parse_args()
    from .account_hosting import account_home
    print(json.dumps(prepare_account_index(account_home("codex", args.account_id))))


if __name__ == "__main__":
    main()
