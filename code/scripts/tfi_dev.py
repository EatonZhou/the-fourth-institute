#!/usr/bin/env python3
"""Local development worktree and main-only publication for the shared repository."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys


def run(root, *args):
    return subprocess.run(args, cwd=root, text=True, capture_output=True, check=True).stdout.strip()


def git(root, *args):
    return run(root, "git", *args)


def configuration(root):
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    path = common / "tfi-development.json"
    if not path.exists():
        raise ValueError("Run tfi_dev.py init from the main checkout first")
    return json.loads(path.read_text())


def clean(root):
    if git(root, "status", "--porcelain"):
        raise ValueError(f"Uncommitted changes must be completed by their owners: {root}")


def branch(root, expected):
    if git(root, "branch", "--show-current") != expected:
        raise ValueError(f"{root} must be on {expected}")


@contextmanager
def lock(root):
    # Both worktrees share this lock; never silently retry a concurrent publisher.
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    path = common / "tfi-development.lock"
    path.mkdir()
    try:
        yield
    finally:
        path.rmdir()


def init(root, dev=None):
    root = Path(git(root, "rev-parse", "--show-toplevel")).resolve()
    branch(root, "main")
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    path = common / "tfi-development.json"
    if path.exists():
        return configuration(root)
    dev = Path(dev or root.with_name(root.name + "-dev")).resolve()
    if dev.exists() or dev == root or root in dev.parents:
        raise ValueError("Dev must be a new sibling directory outside the main checkout")
    hook = common / "hooks/pre-push"
    configured = subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=root,
                                capture_output=True, text=True)
    if configured.stdout.strip() or hook.exists():
        raise ValueError("Existing Git hooks must be integrated explicitly")
    git(root, "worktree", "add", "-b", "dev", str(dev), "HEAD")
    hook.parent.mkdir(exist_ok=True)
    hook.write_text("""#!/bin/sh
# Local policy: only the main branch may be sent to a remote.
while read local_ref local_sha remote_ref remote_sha; do
  if [ "$local_ref" != refs/heads/main ] || [ "$remote_ref" != refs/heads/main ]; then
    echo "TFI: only main:main can be pushed; publish local dev changes first." >&2
    exit 1
  fi
done
""")
    hook.chmod(0o755)
    git(root, "config", "push.default", "simple")
    git(root, "config", "remote.origin.push", "refs/heads/main:refs/heads/main")
    config = {"schema": "tfi.local-development.v1", "main": str(root), "dev": str(dev),
              "remote": "origin", "code_directory": "code"}
    path.write_text(json.dumps(config, indent=2) + "\n")
    return config


def sync(root):
    cfg = configuration(root)
    main, dev = Path(cfg["main"]), Path(cfg["dev"])
    with lock(main):
        branch(main, "main")
        branch(dev, "dev")
        clean(main)
        clean(dev)
        git(dev, "merge", "--no-edit", "main")
    return cfg


def publish(root, message, check, push=False):
    """Publish a clean, tested Dev tree; keep its intermediate commits local."""
    cfg = configuration(root)
    main, dev = Path(cfg["main"]), Path(cfg["dev"])
    if not message.strip() or not check:
        raise ValueError("A message and an explicit verification command are required")
    with lock(main):
        branch(main, "main")
        branch(dev, "dev")
        clean(main)
        clean(dev)
        base, source = git(main, "rev-parse", "HEAD"), git(dev, "rev-parse", "HEAD")
        git(dev, "merge-base", "--is-ancestor", base, source)
        if not git(main, "diff", "--name-only", base, source):
            raise ValueError("No changes to publish")
        subprocess.run(check, cwd=dev, check=True)
        clean(main)
        clean(dev)
        if git(main, "rev-parse", "HEAD") != base or git(dev, "rev-parse", "HEAD") != source:
            raise ValueError("The checked source changed during verification")
        git(main, "merge", "--squash", source)
        git(main, "commit", "-m", message)
        commit = git(main, "rev-parse", "HEAD")
        # Main is the next baseline. Dev's private intermediate commits remain local.
        git(dev, "merge", "--no-edit", "main")
        if push:
            git(main, "push", cfg["remote"], "main:main")
        return {"commit": commit, "source": source, "pushed": push,
                "activation_required": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("init")
    p.add_argument("--dev", type=Path)
    sub.add_parser("status")
    sub.add_parser("sync")
    p = sub.add_parser("publish")
    p.add_argument("--message", required=True)
    p.add_argument("--push", action="store_true")
    p.add_argument("--check", nargs=argparse.REMAINDER, required=True,
                   help="Verification command run in Dev before publication; must be last")
    a = parser.parse_args(argv)
    try:
        if a.action == "init":
            result = init(a.repo, a.dev)
        elif a.action == "sync":
            result = sync(a.repo)
        elif a.action == "publish":
            result = publish(a.repo, a.message, a.check, a.push)
        else:
            result = configuration(a.repo)
            result["worktrees"] = git(a.repo, "worktree", "list", "--porcelain")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, FileExistsError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError):
            print(exc.stderr or "", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
