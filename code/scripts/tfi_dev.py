#!/usr/bin/env python3
"""Local development worktree and main-only publication for the shared repository."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import shlex
from pathlib import Path, PurePosixPath
import subprocess
import sys

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from management_common.instance_manifest import load

REMOTE = "origin"
CODE_DIRECTORY = "code"


def run(root, *args):
    return subprocess.run(args, cwd=root, text=True, capture_output=True, check=True).stdout.strip()


def git(root, *args):
    return run(root, "git", *args)


def configuration():
    """The worktrees of the selected instance: Release is main, Dev is dev.

    Each deployment's code root in instance.json is the code directory of its
    worktree; nothing is recorded in the Git directory.
    """
    instance = load()
    if "dev" not in instance.deployments:
        raise ValueError("The selected instance has no Dev deployment")
    roots = {name: instance.code_root(name) for name in ("release", "dev")}
    if any(root.name != CODE_DIRECTORY for root in roots.values()):
        raise ValueError("Each deployment's code root must be the code directory of its worktree")
    return {"main": str(roots["release"].parent), "dev": str(roots["dev"].parent),
            "remote": REMOTE, "code_directory": CODE_DIRECTORY, "runtime_root": str(instance.root)}


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
    """Create the Dev worktree beside the main checkout; register it with
    ``tfi_development_runtime.py configure``."""
    root = Path(git(root, "rev-parse", "--show-toplevel")).resolve()
    branch(root, "main")
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    dev = Path(dev or root.with_name(root.name + "-dev")).resolve()
    if dev.exists() or dev == root or root in dev.parents:
        raise ValueError("Dev must be a new sibling directory outside the main checkout")
    hook = common / "hooks/pre-push"
    configured = subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=root,
                                capture_output=True, text=True)
    if configured.stdout.strip() or hook.exists():
        raise ValueError("Existing Git hooks must be integrated explicitly")
    git(root, "worktree", "add", "-b", "dev", str(dev), "HEAD")
    install_push_hook(root)
    git(root, "config", "push.default", "simple")
    git(root, "config", "remote.origin.push", "refs/heads/main:refs/heads/main")
    return {"main": str(root), "dev": str(dev)}


def install_push_hook(root):
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    hook = common / "hooks/pre-push"
    configured = subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=root,
                                capture_output=True, text=True)
    if configured.stdout.strip() or (hook.exists() and "# Local policy: only the main branch" not in hook.read_text()):
        raise ValueError("Existing Git hooks must be integrated explicitly")
    hook.parent.mkdir(exist_ok=True)
    hook.write_text("#!/bin/sh\n# Local policy: only the main branch may be sent to a remote.\n"
                    + 'if [ "$(git rev-parse --show-toplevel)" != ' + shlex.quote(str(Path(root).resolve())) + ' ]; then\n'
                    + '  echo "TFI: push from the Release worktree only." >&2\n  exit 1\nfi\n'
                    + """
# Local policy: only the main branch may be sent to a remote.
while read local_ref local_sha remote_ref remote_sha; do
  if [ "$local_ref" != refs/heads/main ] || [ "$remote_ref" != refs/heads/main ]; then
    echo "TFI: only main:main can be pushed; publish local dev changes first." >&2
    exit 1
  fi
done
""")
    hook.chmod(0o755)


def sync():
    cfg = configuration()
    main, dev = Path(cfg["main"]), Path(cfg["dev"])
    with lock(main):
        branch(main, "main")
        branch(dev, "dev")
        # Release only advances along Dev's history. No merge-back or second
        # development history is created, and concurrent working edits stay put.
        git(dev, "merge-base", "--is-ancestor", "main", "dev")
    return cfg


def release_unit_paths(dev, cfg, paths):
    """Add the rest of each browser compatibility unit a selected path belongs to."""
    prefix = cfg["code_directory"]
    code = dev / prefix
    if not paths or not code.is_dir():
        return tuple(paths)
    from scripts.portal_release_units import expand_release_unit_paths

    requested = {PurePosixPath(path).as_posix() for path in paths}
    inside = [path[len(prefix) + 1:] for path in requested if path.startswith(prefix + "/")]
    if not inside:
        return tuple(paths)
    added = [f"{prefix}/{path}" for path in expand_release_unit_paths(inside, code)]
    return (*paths, *(path for path in added if path not in requested))


def publish(message, check=None, push=True, *, paths=(), all_files=False, activate=True):
    """Commit selected live Dev paths and fast-forward the Release worktree."""
    cfg = configuration()
    main, dev = Path(cfg["main"]), Path(cfg["dev"])
    if not message.strip():
        raise ValueError("A publication message is required")
    with lock(main):
        branch(main, "main")
        branch(dev, "dev")
        base = git(main, "rev-parse", "HEAD")
        git(dev, "merge-base", "--is-ancestor", base, "HEAD")
        if check:
            subprocess.run(check, cwd=dev, check=True)
        selected = tuple(paths) if all_files else release_unit_paths(dev, cfg, paths)
        if selected or all_files:
            from scripts.portal_dev_commit import commit as commit_paths
            source = commit_paths(dev, selected, message, all_files=all_files)
        else:
            # A caller that already committed its changes can publish that head.
            source = git(dev, "rev-parse", "HEAD")
        changed = git(dev, "diff", "--name-only", base, source).splitlines()
        environments = require_dependency_environments(dev, main, source, cfg['code_directory'])
        # Git refuses only actual checkout collisions; unrelated uncommitted
        # files in either worktree are not a publication gate.
        git(main, "merge", "--ff-only", source)
        commit = source
        if push:
            git(main, "push", cfg["remote"], "main:main")
        result = {"ok": True, "status": "published", "commit": commit, "source": source, "pushed": push,
                  "paths": changed, "push": {"ok": push, "remote": cfg["remote"]},
                  "changed_paths": changed, "activation_required": bool(changed),
                  "release_unit_paths": sorted(set(selected) - set(paths))}
        result['dependency_environments'] = environments
    if activate:
        from scripts.tfi_development_runtime import request_activation
        result["activation"] = request_activation(Path(cfg["runtime_root"]), changed, commit, parent_commit=base)
        result["health"] = {"status": "pending", "receipt": result["activation"]["receipt"]}
        result["activation_required"] = True
    return result


def require_dependency_environments(dev, main, source, code_directory):
    """Refuse before Release advances, even when activation is deferred.

    Shared services start from live Dev. Release starts from the committed tree,
    including staged work and unpublished ancestors, not Dev's unrelated edits.
    """
    from management_common.code_environment import (
        dependency_source_paths, environment_key, environment_key_from_sources,
    )

    prefix = code_directory + '/'
    names = [name[len(prefix):] for name in git(dev, 'ls-tree', '-r', '--name-only', source, '--', code_directory).splitlines()]
    sources = {}
    for name in dependency_source_paths(names):
        sources[name] = subprocess.run(['git', 'show', f'{source}:{prefix}{name}'], cwd=dev,
                                       capture_output=True, check=True).stdout
    lockfile = main / 'dependencies/python-wheels/requirements.lock'
    keys = {'dev': environment_key(dev / code_directory),
            'release': environment_key_from_sources(sources, wheel_lock=lockfile.read_bytes() if lockfile.is_file() else None)}
    result, missing = {}, []
    for name, root in (('dev', dev), ('release', main)):
        environment = root / 'dependencies' / keys[name] / 'venv'
        python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        result[name] = {'fingerprint': keys[name], 'python': str(python)}
        if not python.is_file():
            missing.append(str(environment))
    if missing:
        raise ValueError('Dependency environment missing; Release has not advanced. '
                         f'Prepare the environments for committed source {source} and live Dev before retrying: '
                         + ', '.join(missing))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2],
                        help="The main checkout; used by init")
    parser.add_argument("--instance", help="Instance name in the OS user's registry; otherwise use PORTAL_INSTANCE_ROOT")
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("init")
    p.add_argument("--dev", type=Path)
    sub.add_parser("status")
    sub.add_parser("sync")
    p = sub.add_parser("publish")
    p.add_argument("--message", required=True)
    p.add_argument("--push", action="store_true", default=True)
    p.add_argument("--no-push", action="store_false", dest="push")
    p.add_argument("--path", action="append", default=[])
    p.add_argument("--all", action="store_true", dest="all_files")
    p.add_argument("--no-activate", action="store_true")
    p.add_argument("--check", nargs=argparse.REMAINDER,
                   help="Optional relevant verification command; must be last")
    a = parser.parse_args(argv)
    try:
        if a.instance:
            from management_common import host_instances
            os.environ["PORTAL_INSTANCE_ROOT"] = str(host_instances.find(a.instance).root)
        if a.action == "init":
            result = init(a.repo, a.dev)
        elif a.action == "sync":
            result = sync()
        elif a.action == "publish":
            result = publish(a.message, a.check, a.push, paths=a.path,
                             all_files=a.all_files, activate=not a.no_activate)
        else:
            result = configuration()
            result["worktrees"] = git(result["main"], "worktree", "list", "--porcelain")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, FileExistsError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError):
            print(exc.stderr or "", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
