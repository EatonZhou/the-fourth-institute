"""Import legacy instance avatars into the active instance's PostgreSQL.

Run with the instance environment. Without --apply this validates files only.
Existing database records (including deletions) always take precedence.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
from itertools import batched
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from management_portal.avatar_store import MAX_IMAGE_BYTES, ScopeAvatarStore, avatar_root
from management_portal.avatar_database import DatabaseAvatarStore, database
from management_portal.user_avatar import UserAvatarStore
from management_portal.resident_avatar import ResidentAvatarStore


def migrate(*, apply=False):
    result = {"apply": apply, "stores": {}, "errors": []}
    for kind, factory in (("user", UserAvatarStore), ("resident", ResidentAvatarStore), ("scope", ScopeAvatarStore)):
        legacy = factory(avatar_root(kind))
        store = DatabaseAvatarStore(legacy, kind)
        report = {"records": 0, "image_bytes": 0, "verified": 0, "preserved": 0}
        result["stores"][kind] = report
        # Bound transaction and advisory-lock size, while avoiding one disk
        # sync per record on instances with thousands of Resident avatars.
        for batch in batched(sorted(legacy.root.glob("*.json")), 200):
            with database().transaction() if apply else nullcontext():
                for path in batch:
                    try:
                        record = json.loads(path.read_text(encoding="utf-8"))
                        subject = record[legacy.identity_field]
                        if legacy._path(subject) != path:
                            raise ValueError("Record identity does not match its filename")
                        avatar = legacy.read(subject)
                        content = None
                        if avatar["kind"] == "upload":
                            content = legacy.image(subject, avatar["version"])
                            if not 0 < len(content) <= MAX_IMAGE_BYTES or hashlib.sha256(content).hexdigest() != avatar["version"]:
                                raise ValueError("Image hash mismatch")
                            report["image_bytes"] += len(content)
                        report["records"] += 1
                        if apply:
                            saved = store.import_legacy(subject)
                            if saved != avatar:
                                report["preserved"] += 1
                            else:
                                if content is not None and store.image(subject, avatar["version"]) != content:
                                    raise ValueError("Database image differs from legacy bytes")
                                report["verified"] += 1
                    except (ValueError, OSError, KeyError, TypeError) as exc:
                        result["errors"].append({"kind": kind, "file": path.name, "error": str(exc)})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = migrate(apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
