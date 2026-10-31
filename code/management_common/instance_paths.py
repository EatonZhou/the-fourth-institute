from __future__ import annotations

import argparse
from pathlib import Path
import sys

# Deployment shell entrypoints invoke this file directly from arbitrary cwd.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from management_common import portal_paths


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "portal_instances.json"


def instance_root() -> Path:
    return portal_paths.path("instances_root")


def secrets_dir() -> Path:
    return portal_paths.path("secrets_dir")


def is_instance_state(path: Path, source_root: Path) -> bool:
    candidate = path.resolve(strict=False)
    source = source_root.resolve(strict=False)
    roots = (instance_root().resolve(strict=False), (source / "portal_instances").resolve(strict=False))
    for root in roots:
        if not candidate.is_relative_to(root):
            continue
        if source.is_relative_to(root) and candidate.is_relative_to(source):
            continue
        return True
    return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", choices=("dev", "release"))
    arguments = parser.parse_args()
    print(instance_root() / arguments.instance if arguments.instance else instance_root())
