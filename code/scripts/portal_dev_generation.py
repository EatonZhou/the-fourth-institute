#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from management_common.instance_paths import instance_root, is_instance_state
from management_portal.shared_state import atomic_replace_json

DEV_INSTANCE_ROOT = instance_root() / 'dev'
DEV_GENERATION_ENV = DEV_INSTANCE_ROOT / "source-generation.env"
DEV_COMPONENT_GENERATIONS = DEV_INSTANCE_ROOT / "component-generations.json"
COMPONENTS = (
    "web",
    "resource_service",
    "execution_service",
    "execution_worker",
    "society_service",
)
WATCH_ROOTS = (
    "knowledge_board_lan",
    "management_common",
    "management_portal",
    "project_board_lan",
    "scripts",
    "skill_board_lan",
    "systemd",
    "tests",
)
WATCH_FILES = (
    "config/portal_instances.json",
    "package.json",
    "requirements-portal-runtime.txt",
)
IGNORED_NAMES = {
    "__pycache__",
    "restart_notice.json",
    "restart_notice_request.json",
}
IGNORED_SUFFIXES = {".pyc", ".pyo", ".swp", ".tmp", "~"}


def source_paths(root: Path = ROOT) -> list[Path]:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        candidates = [
            path
            for relative in WATCH_ROOTS
            for path in (root / relative).rglob("*")
            if (root / relative).is_dir()
        ]
        candidates.extend(root / relative for relative in WATCH_FILES)
    else:
        candidates = [root / line for line in result.stdout.splitlines() if line]

    watched_roots = set(WATCH_ROOTS)
    watched_files = set(WATCH_FILES)
    paths: list[Path] = []
    for path in candidates:
        if is_instance_state(path, root):
            continue
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.as_posix() not in watched_files and (
            not relative.parts or relative.parts[0] not in watched_roots
        ):
            continue
        if any(part in IGNORED_NAMES for part in relative.parts):
            continue
        if any(path.name.endswith(suffix) for suffix in IGNORED_SUFFIXES):
            continue
        paths.append(path)
    return sorted(set(paths), key=lambda path: path.relative_to(root).as_posix())


def source_state(root: Path = ROOT) -> tuple[tuple[str, int, int], ...]:
    state: list[tuple[str, int, int]] = []
    for path in source_paths(root):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        state.append(
            (path.relative_to(root).as_posix(), stat.st_mtime_ns, stat.st_size)
        )
    return tuple(state)


def source_build_id(root: Path = ROOT) -> str:
    digest = hashlib.sha256()
    for path in source_paths(root):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except FileNotFoundError:
            continue
        digest.update(b"\0")
    return f"dev-{digest.hexdigest()[:16]}"


def read_build_id(path: Path = DEV_GENERATION_ENV) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key.strip() == "PORTAL_BUILD_ID":
            return value.strip()
    return ""


def read_component_build_ids(
    path: Path = DEV_COMPONENT_GENERATIONS,
) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    values = payload.get("components") if isinstance(payload, dict) else {}
    if not isinstance(values, dict):
        return {}
    return {
        str(component): str(build_id)
        for component, build_id in values.items()
        if str(component) in COMPONENTS and str(build_id)
    }


def _write_component_build_ids(
    values: dict[str, str],
    path: Path = DEV_COMPONENT_GENERATIONS,
) -> None:
    atomic_replace_json(
        path,
        {"schema": "tfi.portal-component-generations.v1", "components": values},
    )


def activate(
    root: Path = ROOT,
    path: Path = DEV_GENERATION_ENV,
    *,
    components: tuple[str, ...] | list[str] | None = None,
) -> str:
    build_id = source_build_id(root)
    previous_build_id = read_build_id(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(f"PORTAL_BUILD_ID={build_id}\n", encoding="utf-8")
    os.replace(temporary, path)
    selected = COMPONENTS if components is None else tuple(components)
    unknown = sorted(set(selected) - set(COMPONENTS))
    if unknown:
        raise ValueError(f"unknown Portal components: {unknown}")
    component_path = path.with_name(DEV_COMPONENT_GENERATIONS.name)
    values = read_component_build_ids(component_path)
    if not values:
        values = {
            component: previous_build_id or build_id
            for component in COMPONENTS
        }
    for component in selected:
        values[component] = build_id
    _write_component_build_ids(values, component_path)
    return build_id


def main() -> int:
    print(activate())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
