#!/usr/bin/env python3
"""Skill board backend for the management portal."""
from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml
from management_common.scan_cancellation import check_scan_cancelled, walk_paths
from management_common.portal_paths import path as portal_path
from management_common.text_values import strip_quotes

from management_portal import project_registry

PROJECTS_ROOT = portal_path("share_root") / "Projects"
SHARED_SKILLS_ROOT = portal_path("share_root") / "Skills"
THIRD_PARTY_SKILLS_ROOT = SHARED_SKILLS_ROOT / "third-party"

_MD_CACHE: dict[str, dict[str, Any]] = {}
_TEXT_FILE_EXTENSIONS = {
    ".css",
    ".csv",
    ".html",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
_IMAGE_FILE_EXTENSIONS = {
    ".avif",
    ".bmp",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".webp",
}


def _parse_front_matter(md_text: str) -> tuple[dict[str, str], str]:
    text = md_text.lstrip("\ufeff")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    fm_lines: list[str] = []
    end_index = None
    for i in range(1, min(len(lines), 400)):
        if lines[i].strip() == "---":
            end_index = i
            break
        fm_lines.append(lines[i])
    if end_index is None:
        return {}, text

    meta: dict[str, str] = {}
    for raw in fm_lines:
        if ":" not in raw:
            continue
        k, v = raw.split(":", 1)
        meta[k.strip()] = strip_quotes(v.strip())
    rest = "\n".join(lines[end_index + 1:])
    return meta, rest


def _read_text(path: Path, max_bytes: int = 2_000_000) -> str:
    try:
        size = path.stat().st_size
    except (OSError, ValueError):  # missing or unreadable file, or a NUL in the path
        size = 0
    try:
        if size and size > max_bytes:
            return path.read_bytes()[:max_bytes].decode("utf-8", errors="replace")
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return ""


def _file_id_for_path(path: Path) -> str:
    return hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:16]


def _first_heading(text: str) -> str:
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith("# ") and not s.startswith("##"):
            return s[2:].strip()
        if s.startswith("#"):
            return ""
    return ""


def _first_non_empty_non_heading_line(text: str) -> str:
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("-"):
            s = s[1:].strip()
        if s:
            return s
    return ""


def _title_from_filename(path: Path, item_type: str = "skill") -> str:
    name = path.name
    marker = f"__{item_type}__"
    suffix = f"__{item_type}_"
    if marker in name and suffix in name:
        title_part = name.split(marker, 1)[1].split(suffix, 1)[0].strip()
        if title_part:
            return title_part
    if path.name.upper() == "SKILL.MD" and path.parent.name:
        return path.parent.name
    return path.stem


def _is_index_file(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(".index.md") or name == "index.md.index.md"


def _is_ignored_skill_file(path: Path, root: Path | None = None) -> bool:
    parts = path.parts
    if root is not None:
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            pass
    if any(part.startswith(".") for part in parts):
        return True
    if "__pycache__" in path.parts:
        return True
    return _is_index_file(path)


def _candidate_markdown_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix.lower() == ".md" and not _is_ignored_skill_file(root, root.parent) else []
    if not root.is_dir():
        return []
    files = [
        p for p in walk_paths(root)
        if p.name.endswith(".md") and p.is_file() and not _is_ignored_skill_file(p, root)
    ]
    return sorted(files, key=lambda p: str(p.relative_to(root)).lower())


def _candidate_skill_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if not _is_ignored_skill_file(root, root.parent) else []
    if not root.is_dir():
        return []
    files = [
        p for p in walk_paths(root)
        if p.is_file()
        and not _is_ignored_skill_file(p, root)
    ]
    return sorted(files, key=lambda p: str(p.relative_to(root)).lower())


def _file_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".md":
        return "markdown"
    if suffix in _IMAGE_FILE_EXTENSIONS:
        return "image"
    if suffix in _TEXT_FILE_EXTENSIONS:
        return "text"
    return "binary"


def _manifest_primary_file(root: Path, files: list[Path]) -> Path | None:
    if not root.is_dir():
        return None
    manifests = [root / "skill.yaml", root / "skill.yml"]
    present_manifests = [path for path in manifests if path.is_file()]
    if not present_manifests:
        return None
    if len(present_manifests) > 1:
        raise ValueError("Skill package must not contain both skill.yaml and skill.yml")
    manifest_path = present_manifests[0]
    try:
        payload = yaml.safe_load(_read_text(manifest_path, max_bytes=100_000)) or {}
    except Exception as exc:
        raise ValueError(f"invalid Skill manifest: {manifest_path.name}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"invalid Skill manifest: {manifest_path.name}")
    primary_file = str(payload.get("primary_file") or "").strip()
    if not primary_file:
        raise ValueError("Skill manifest requires primary_file")
    candidate = (root / primary_file).resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    if not candidate.is_relative_to(resolved_root) or candidate.suffix.lower() != ".md":
        raise ValueError("Skill manifest primary_file must reference package Markdown")
    allowed = {path.resolve(strict=False) for path in files}
    if candidate not in allowed or not candidate.is_file():
        raise ValueError("Skill manifest primary_file does not exist in the package")
    return candidate


def _choose_primary_file(root: Path, files: list[Path]) -> Path | None:
    if not files:
        return None
    if root.is_file():
        return root if root in files else files[0]
    declared = _manifest_primary_file(root, files)
    if declared is not None:
        return declared
    top_level = [path for path in files if path.parent == root]
    skill_md = next((path for path in top_level if path.name == "SKILL.md"), None)
    if skill_md is not None:
        return skill_md
    package_markdown = next(
        (
            path for path in top_level
            if path.stem.casefold() == root.name.casefold()
        ),
        None,
    )
    if package_markdown is not None:
        return package_markdown
    if top_level:
        return sorted(top_level, key=lambda path: path.name.casefold())[0]
    return sorted(files, key=lambda path: str(path.relative_to(root)).casefold())[0]


def _parse_projects(active_only: bool = False) -> list[dict[str, Any]]:
    projects: list[dict[str, Any]] = []
    for p in project_registry.projects():
        pid = str(p.get("id") or "").strip()
        if not pid:
            continue
        status = str(p.get("status") or "")
        if active_only and status != "active":
            continue
        raw_path = str(p.get("path") or "").strip()
        if raw_path.startswith("/Projects/"):
            path = str(PROJECTS_ROOT / raw_path[len("/Projects/"):])
        elif raw_path == "/Projects":
            path = str(PROJECTS_ROOT)
        else:
            path = raw_path or str(PROJECTS_ROOT / pid)
        projects.append({
            "id": pid,
            "status": status,
            "path": path,
        })
    return projects


def _safe_rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:  # not under root
        return path.name


def _build_item(
    *,
    root: Path,
    primary_path: Path,
    files: list[Path],
    project: str,
    source: str,
    source_label: str,
) -> dict[str, Any]:
    text = _read_text(primary_path)
    meta, body = _parse_front_matter(text)
    is_project_knowledge_entry = source == "project" and root.is_file()
    skill_id = str(
        meta.get("kb_id")
        if is_project_knowledge_entry
        else meta.get("id")
        or ""
    )
    name = str(meta.get("name") or "").strip() or root.name or primary_path.stem
    title = (
        _first_heading(body)
        or name
        or _title_from_filename(primary_path)
        or root.name
    )
    description = str(meta.get("description") or "").strip()
    preview = description or _first_non_empty_non_heading_line(body)
    if len(preview) > 260:
        preview = preview[:260].rstrip() + "..."
    try:
        importance = int(str(meta.get("importance") or "0"))
    except ValueError:  # not an integer
        importance = 0
    try:
        mtime = int(primary_path.stat().st_mtime)
    except (OSError, ValueError):
        mtime = 0
    file_id = _file_id_for_path(primary_path)
    companion_files = [
        {
            "file_id": _file_id_for_path(p),
            "name": _safe_rel(p, root),
            "relative_path": _safe_rel(p, root),
            "kind": _file_kind(p),
        }
        for p in files
        if p != primary_path
    ]
    skill_files = [
        {
            "file_id": _file_id_for_path(p),
            "name": _safe_rel(p, root),
            "relative_path": _safe_rel(p, root),
            "kind": _file_kind(p),
            "primary": p == primary_path,
        }
        for p in files
    ]
    return {
        "file_id": file_id,
        "skill_id": skill_id,
        "name": name,
        "title": title,
        "description": description,
        "preview": preview,
        "project": project,
        "source": source,
        "source_label": source_label,
        "created_at": str(meta.get("created_at") or ""),
        "status": str(meta.get("status") or ""),
        "importance": importance,
        "label": str(meta.get("label") or ""),
        "filename": primary_path.name,
        "path": str(primary_path),
        "root_path": str(root),
        "relative_path": _safe_rel(primary_path, root),
        "mtime": mtime,
        "file_count": len(files),
        "files": skill_files,
        "companion_files": companion_files,
    }


def _get_item_cached(root: Path, primary_path: Path, files: list[Path], project: str, source: str, source_label: str) -> dict[str, Any]:
    abs_path = primary_path.resolve()
    try:
        mtime = abs_path.stat().st_mtime
    except OSError:  # the file went away
        _MD_CACHE.pop(str(abs_path), None)
        return {}
    cache_key = str(abs_path)
    files_signature = [
        (str(p.resolve()), int(p.stat().st_mtime) if p.exists() else 0)
        for p in files
    ]
    cached = _MD_CACHE.get(cache_key)
    if (
        cached
        and cached.get("mtime") == mtime
        and cached.get("files_signature") == files_signature
        and cached.get("project") == project
        and cached.get("source") == source
        and isinstance(cached.get("item"), dict)
    ):
        return dict(cached["item"])
    item = _build_item(root=root, primary_path=abs_path, files=[p.resolve() for p in files], project=project, source=source, source_label=source_label)
    _MD_CACHE[cache_key] = {
        "mtime": mtime,
        "files_signature": files_signature,
        "project": project,
        "source": source,
        "item": dict(item),
    }
    return item


def _iter_shared_skill_roots() -> list[Path]:
    if not SHARED_SKILLS_ROOT.is_dir():
        return []
    roots = [
        p for p in SHARED_SKILLS_ROOT.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p != THIRD_PARTY_SKILLS_ROOT
    ]
    roots.extend(p for p in SHARED_SKILLS_ROOT.iterdir() if p.is_file() and p.suffix.lower() == ".md" and not p.name.startswith("."))
    return sorted(roots, key=lambda p: p.name.lower())


def _iter_third_party_skill_roots() -> list[Path]:
    if not THIRD_PARTY_SKILLS_ROOT.is_dir():
        return []
    roots: list[Path] = []
    for child in sorted(THIRD_PARTY_SKILLS_ROOT.iterdir(), key=lambda p: p.name.lower()):
        if child.name.startswith("."):
            continue
        if child.is_dir():
            roots.append(child)
        elif child.is_file() and child.suffix.lower() == ".md" and not _is_index_file(child):
            roots.append(child)
    return roots


def _iter_project_skill_roots(project_path: Path) -> list[Path]:
    skill_root = project_path / "knowledge_base" / "skill"
    if not skill_root.is_dir():
        return []
    roots: list[Path] = []
    for child in sorted(skill_root.iterdir(), key=lambda p: p.name.lower()):
        if child.name.startswith("."):
            continue
        if child.is_dir():
            roots.append(child)
        elif child.is_file() and child.suffix.lower() == ".md" and not _is_index_file(child):
            roots.append(child)
    return roots


def scan_index_source(active_only: bool = False) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    resource_errors: list[dict[str, str]] = []

    def append_root(
        root: Path,
        *,
        project: str,
        source: str,
        source_label: str,
    ) -> None:
        check_scan_cancelled()
        try:
            markdown_files = _candidate_markdown_files(root)
            primary = _choose_primary_file(root, markdown_files)
            if not primary:
                return
            files = _candidate_skill_files(root) or markdown_files
            item = _get_item_cached(
                root=root,
                primary_path=primary,
                files=files,
                project=project,
                source=source,
                source_label=source_label,
            )
        except Exception as exc:
            resource_errors.append(
                {
                    "path": str(root),
                    "error": str(exc)[:1000],
                }
            )
            return
        if not item:
            return
        item.pop("path", None)
        items.append(item)

    for root in _iter_shared_skill_roots():
        append_root(
            root,
            project="",
            source="shared",
            source_label="Shared",
        )

    for root in _iter_third_party_skill_roots():
        append_root(
            root,
            project="",
            source="third_party",
            source_label="Third-party",
        )

    projects = _parse_projects(active_only=active_only)
    for project in projects:
        project_id = str(project.get("id") or "")
        project_path = Path(str(project.get("path") or ""))
        for root in _iter_project_skill_roots(project_path):
            append_root(
                root,
                project=project_id,
                source="project",
                source_label="Project",
            )

    source_rank = {"shared": 0, "project": 1, "third_party": 2}
    items.sort(
        key=lambda x: (
            source_rank.get(str(x.get("source") or ""), 1),
            -int(x.get("mtime") or 0),
            str(x.get("title") or "").lower(),
        )
    )
    return {
        "generated_at": int(time.time()),
        "projects": projects,
        "sources": ["shared", "project", "third_party"],
        "items": items,
        "resource_errors": resource_errors,
    }


def _path_is_allowed(path: Path, *, text_only: bool = False) -> bool:
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError, ValueError):  # unreadable, a symlink loop, or a NUL in the path
        return False
    allowed_roots = [
        SHARED_SKILLS_ROOT.resolve(),
        PROJECTS_ROOT.resolve(),
    ]
    if not any(resolved == root or root in resolved.parents for root in allowed_roots):
        return False
    if text_only and resolved.suffix.lower() not in _TEXT_FILE_EXTENSIONS:
        return False
    parts = resolved.parts
    if SHARED_SKILLS_ROOT.resolve() in [resolved, *resolved.parents]:
        if _is_ignored_skill_file(resolved):
            return False
        return True
    if _is_ignored_skill_file(resolved):
        return False
    return "knowledge_base" in parts and "skill" in parts


def _resolve_skill_record(skill_id: str, *, actor: str = "") -> dict[str, Any]:
    from management_portal.resources import get_resource_service

    try:
        get_resource_service().validate_id(skill_id, "skill")
    except ValueError:
        raise ValueError("canonical skill address required")
    record = get_resource_service().get(skill_id, actor=actor)
    if "skill" not in set(record.get("facets") or []):
        raise KeyError("skill not found")
    return record


def _resolve_skill_address(address: str, *, actor: str = "") -> tuple[str, Path]:
    clean = str(address or "").strip().strip("/")
    skill_id, separator, suffix = clean.partition("/")
    record = _resolve_skill_record(skill_id, actor=actor)
    attrs = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
    if not separator:
        path = Path(str((record.get("source") or {}).get("path") or ""))
    else:
        parts = suffix.split("/")
        if len(parts) < 2 or parts[0] != "file":
            raise ValueError("Skill nested address must use /file/<relative-path>")
        relative = Path(*[unquote(part) for part in parts[1:]])
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError("invalid Skill file address")
        root = Path(str(attrs.get("content_root") or "")).resolve(strict=False)
        path = (root / relative).resolve(strict=False)
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("invalid Skill file address") from exc
    if not path.exists() or not path.is_file():
        raise KeyError("skill file not found")
    return skill_id, path


def _direct_child(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    return len(relative.parts) == 1 and not relative.parts[0].startswith(".")


def _skill_trash_root(target: Path) -> Path:
    resolved = target.resolve(strict=True)
    shared_root = SHARED_SKILLS_ROOT.resolve(strict=False)
    third_party_root = THIRD_PARTY_SKILLS_ROOT.resolve(strict=False)
    if _direct_child(resolved, third_party_root):
        return THIRD_PARTY_SKILLS_ROOT / ".trash"
    if _direct_child(resolved, shared_root) and resolved.name != THIRD_PARTY_SKILLS_ROOT.name:
        return SHARED_SKILLS_ROOT / ".trash"

    for project in _parse_projects(active_only=False):
        project_path = Path(str(project.get("path") or "")).expanduser()
        skill_root = (project_path / "knowledge_base" / "skill").resolve(strict=False)
        if _direct_child(resolved, skill_root):
            return project_path / "knowledge_base" / ".trash"
    raise PermissionError("skill package path is not deletable")


def _unique_skill_trash_path(trash_root: Path, target: Path) -> Path:
    candidate = trash_root / target.name
    if not candidate.exists():
        return candidate
    stamp = int(time.time())
    for index in range(1, 1000):
        suffix = f"__deleted_{stamp}" if index == 1 else f"__deleted_{stamp}_{index}"
        name = (
            f"{target.stem}{suffix}{target.suffix}"
            if target.is_file()
            else f"{target.name}{suffix}"
        )
        candidate = trash_root / name
        if not candidate.exists():
            return candidate
    raise RuntimeError("could not allocate Skill trash path")


def delete_skill(address: str, *, actor: str = "") -> tuple[int, dict[str, Any]]:
    clean = str(address or "").strip().strip("/")
    if "/" in clean:
        return 400, {"error": "root skill address required"}
    try:
        record = _resolve_skill_record(clean, actor=actor)
        attrs = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
        source = record.get("source") if isinstance(record.get("source"), dict) else {}
        source_path_text = str(source.get("path") or "").strip()
        content_root_text = str(attrs.get("content_root") or "").strip()
        if not source_path_text:
            raise KeyError("skill file not found")
        source_path = Path(source_path_text).expanduser()
        target = (
            source_path
            if attrs.get("single_file") is True
            else Path(content_root_text or source_path.parent).expanduser()
        )
        if not target.exists():
            raise KeyError("skill package not found")
        if attrs.get("single_file") is True and not target.is_file():
            raise KeyError("skill file not found")
        if attrs.get("single_file") is not True and not target.is_dir():
            raise KeyError("skill package not found")
        trash_root = _skill_trash_root(target)
        trash_root.mkdir(parents=True, exist_ok=True)
        trash_path = _unique_skill_trash_path(trash_root, target)
        original_path = str(target)
        from management_portal.workbench_projections.resource_lifecycle import release_resource
        from management_portal.resources.locking import resource_mutation_lock
        with resource_mutation_lock(source_path.parent, clean):
            release_resource(clean, actor=actor)
            shutil.move(original_path, str(trash_path))
        _MD_CACHE.clear()
    except ValueError as exc:
        return 400, {"error": str(exc)}
    except KeyError as exc:
        return 404, {"error": str(exc)}
    except PermissionError as exc:
        return 403, {"error": str(exc)}
    except OSError as exc:
        return 500, {"error": f"skill delete failed: {exc}"}
    return 200, {
        "ok": True,
        "deleted": True,
        "resource_type": "skill",
        "skill_id": clean,
        "kb_id": clean,
        "path": original_path,
        "trash_path": str(trash_path),
    }


def save_address_text(
    address: str,
    text: str,
    *,
    actor: str = "",
    expected_version: object = None,
) -> tuple[int, dict[str, Any]]:
    """Replace a Skill file; ``expected_version`` is the sha256 that ReadResource returned."""
    try:
        skill_id, path = _resolve_skill_address(address, actor=actor)
    except ValueError as exc:
        return 400, {"error": str(exc)}
    except KeyError as exc:
        return 404, {"error": str(exc)}
    if not _path_is_allowed(path, text_only=True):
        return 403, {"error": "path not allowed"}
    body = str(text or "")
    from management_portal.resources.locking import resource_mutation_lock
    from management_portal.resources.content_changes import record_text_change

    with resource_mutation_lock(path.parent, skill_id):
        current = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected_version is not None and str(expected_version).strip() != current:
            return 409, {"error": "Skill 已被其他修改更新；请重新加载后再保存", "current_version": current}
        before = path.read_text(encoding="utf-8")
        path.write_text(body, encoding="utf-8")
        record_text_change(address, path, before, body)
    _MD_CACHE.pop(str(path.resolve()), None)
    return 200, {
        "ok": True,
        "id": str(address or "").strip(),
        "skill_id": skill_id,
        "path": str(path),
        "mtime": int(path.stat().st_mtime),
    }
