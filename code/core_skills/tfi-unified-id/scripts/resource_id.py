#!/usr/bin/env python3
"""Create, register, scan, and resolve TFI resource IDs."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
from pathlib import Path
from urllib.parse import quote, unquote

# Help and pure address parsing need only the standard library.
SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORTAL_ROOT = None
PORTAL_PYTHON = None
yaml = None


def _source_root() -> Path:
    checkout = next(
        (parent for parent in (*Path(__file__).resolve().parents, Path.cwd())
         if (parent / "management_common" / "site_config.py").is_file()), None
    )
    explicit_root = os.environ.get("PORTAL_ROOT")
    anchor = Path(explicit_root).expanduser().resolve() if explicit_root is not None else (checkout or Path.cwd())
    config_name = os.environ.get("PORTAL_SITE_CONFIG", "").strip()
    if config_name:
        config_path = Path(config_name).expanduser()
        if not config_path.is_absolute():
            config_path = anchor / config_path
        # The common helper must read exactly the same explicit configuration.
        os.environ["PORTAL_SITE_CONFIG"] = str(config_path)
    selected = explicit_root
    root = Path(selected).expanduser().resolve() if selected is not None else checkout
    if root is None or not (root / "management_common" / "product_environment.py").is_file():
        raise RuntimeError("TFI Portal checkout not found; set PORTAL_ROOT explicitly")
    return root


def _configure_portal() -> None:
    global DEFAULT_PORTAL_ROOT, PORTAL_PYTHON, DEFAULT_PROJECTS_ROOT
    global DEFAULT_SKILL_ROOTS, DEFAULT_PAPER_ROOT
    global read_product_env, mirror_product_env
    DEFAULT_PORTAL_ROOT = _source_root()
    sys.path.insert(0, str(DEFAULT_PORTAL_ROOT))
    from management_common.product_environment import read_product_env, mirror_product_env
    from management_common.portal_paths import path as portal_path

    native_python = DEFAULT_PORTAL_ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    PORTAL_PYTHON = Path(read_product_env("PORTAL_PYTHON", str(native_python if native_python.is_file() else sys.executable))).expanduser()
    share = portal_path("share_root")
    DEFAULT_PROJECTS_ROOT = Path(read_product_env("PORTAL_PROJECTS_ROOT", str(share / "Projects"))).expanduser()
    DEFAULT_SKILL_ROOTS = (share / "Skills",)
    DEFAULT_PAPER_ROOT = share / "Papers"


TYPE_PATTERN = r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*"
CODE_PATTERN = r"[0-9a-f]{12}"
ID_RE = re.compile(rf"^(?P<type>{TYPE_PATTERN})_(?P<code>{CODE_PATTERN})$")
RAW_SEGMENT_RE = re.compile(r"^(?:[A-Za-z0-9._~:@+\-]|%[0-9A-Fa-f]{2})+$")
PATH_SAFE = "-._~:@+"
COLLABORATION_TYPES = frozenset({"resident", "lineage", "workspace"})
DEFAULT_CONTEXT_MAX_MESSAGES = 160
DEFAULT_CONTEXT_MAX_CHARS = 120_000


class AddressError(ValueError):
    """Raised when an ID or nested address is not canonical."""


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _portal_backends() -> tuple[object, object, object, object]:
    if not (DEFAULT_PORTAL_ROOT / "management_portal").is_dir():
        raise RuntimeError(f"TFI Portal source not found: {DEFAULT_PORTAL_ROOT}")
    portal_root = str(DEFAULT_PORTAL_ROOT)
    if portal_root not in sys.path:
        sys.path.insert(0, portal_root)
    from management_portal import article_identity_backend
    from management_portal import paper_resources
    from management_portal import resource_identity_backend
    from management_portal import search_identity_backend

    return (
        resource_identity_backend,
        article_identity_backend,
        paper_resources,
        search_identity_backend,
    )


def _device_backend() -> object:
    if not (DEFAULT_PORTAL_ROOT / "management_portal").is_dir():
        raise RuntimeError(f"TFI Portal source not found: {DEFAULT_PORTAL_ROOT}")
    portal_root = str(DEFAULT_PORTAL_ROOT)
    if portal_root not in sys.path:
        sys.path.insert(0, portal_root)
    from management_portal import device_identity_backend

    return device_identity_backend


def _portal_resource_service() -> object:
    if not (DEFAULT_PORTAL_ROOT / "management_portal").is_dir():
        raise RuntimeError(f"TFI Portal source not found: {DEFAULT_PORTAL_ROOT}")
    portal_root = str(DEFAULT_PORTAL_ROOT)
    if portal_root not in sys.path:
        sys.path.insert(0, portal_root)
    os.environ.setdefault("PORTAL_RESOURCE_SERVICE_MODE", "client")
    from management_portal.resources import get_resource_service

    return get_resource_service()


def _portal_society_service() -> object:
    if not (DEFAULT_PORTAL_ROOT / "management_portal").is_dir():
        raise RuntimeError(f"TFI Portal source not found: {DEFAULT_PORTAL_ROOT}")
    portal_root = str(DEFAULT_PORTAL_ROOT)
    if portal_root not in sys.path:
        sys.path.insert(0, portal_root)
    from management_portal.society_service import get_society_service

    return get_society_service()


def load_registry(actor: str = "", resident_id: str | None = None) -> dict:
    """Read the deployed declaration catalog through the caller's ISA identity."""
    portal_root = str(DEFAULT_PORTAL_ROOT)
    if portal_root not in sys.path:
        sys.path.insert(0, portal_root)
    injected_value = read_product_env("PORTAL_RESIDENT_ID")
    injected = injected_value.strip() if injected_value is not None else None
    requested = resident_id.strip() if resident_id is not None else None
    if injected == "" or requested == "":
        raise RuntimeError("Resident 身份不能为空；请提供有效的运行环境身份或 --resident-id")
    if injected is not None and requested is not None and injected != requested:
        raise RuntimeError("--resident-id 与 Portal 注入的 Resident 身份不一致")
    if injected is not None or requested is not None:
        from scripts.resident_hatg_instruction import _resident_id, execute
        # Keep the shared ID validator and Resident bearer transport; do not
        # rewrite the caller's environment or fall back to a human identity.
        payload = execute(_resident_id(injected if injected is not None else requested), {
            "instruction": "ListResourceTypes", "arguments": {},
        })["result"]
    else:
        from management_portal import isa_client
        caller = str(actor or read_product_env("PORTAL_RESOURCE_ID_ACTOR") or "").strip()
        if not caller:
            raise RuntimeError("类型目录需要调用身份：Portal Resident 环境、--resident-id 或 --actor <portal-user>")
        payload = isa_client.call(caller, "ListResourceTypes", {}, entry="cli")
    registry = dict(payload["registry"])
    exceptions = registry.get("id_exceptions") or {}
    registry["types"] = {item["type"]: dict(item) for item in payload["types"] if item["type"] not in exceptions}
    if registry.get("code_pattern") != CODE_PATTERN:
        raise RuntimeError("type registry code_pattern does not match the validator")
    if registry.get("type_pattern") != TYPE_PATTERN:
        raise RuntimeError("type registry type_pattern does not match the validator")
    return registry


def registered_types(registry: dict) -> dict:
    types = registry.get("types")
    if not isinstance(types, dict) or not types:
        raise RuntimeError("type registry has no types")
    return types


def registered_id_exceptions(registry: dict) -> dict:
    exceptions = registry.get("id_exceptions")
    return exceptions if isinstance(exceptions, dict) else {}


def validate_type(resource_type: str, registry: dict) -> None:
    if re.fullmatch(TYPE_PATTERN, resource_type) is None:
        raise AddressError(
            f"invalid type {resource_type!r}; use registered lowercase snake_case"
        )
    if resource_type in registered_id_exceptions(registry):
        exception = registered_id_exceptions(registry)[resource_type]
        registered_format = str(exception.get("format") or "registered value")
        raise AddressError(
            f"{resource_type} uses {registered_format}; do not generate {resource_type}_* IDs"
        )
    if resource_type not in registered_types(registry):
        raise AddressError(
            f"unregistered type {resource_type!r}; update the approved type registry first"
        )


def parse_root(root_id: str, registry: dict) -> dict:
    match = ID_RE.fullmatch(root_id)
    if match is None:
        raise AddressError(
            "invalid root ID; expected <registered_type>_<12 lowercase hexadecimal characters>"
        )
    resource_type = match.group("type")
    validate_type(resource_type, registry)
    return {
        "id": root_id,
        "type": resource_type,
        "code": match.group("code"),
        "category": registered_types(registry)[resource_type].get("category", ""),
    }


def canonical_segment(segment: str) -> str:
    if not segment:
        raise AddressError("nested address contains an empty path segment")
    if RAW_SEGMENT_RE.fullmatch(segment) is None:
        raise AddressError(
            f"non-canonical segment {segment!r}; percent-encode spaces, Unicode, /, #, and ?"
        )
    try:
        decoded = unquote(segment, encoding="utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise AddressError(f"segment {segment!r} is not valid percent-encoded UTF-8") from exc
    if decoded in {".", ".."}:
        raise AddressError("nested address must not contain . or .. segments")
    encoded = quote(decoded, safe=PATH_SAFE, encoding="utf-8", errors="strict")
    if encoded != segment:
        raise AddressError(f"segment {segment!r} is not canonical; use {encoded!r}")
    return segment


def parse_address(address: str, registry: dict) -> dict:
    if not address or address != address.strip():
        raise AddressError("address must be non-empty and have no surrounding whitespace")
    parts = address.split("/")
    root = parse_root(parts[0], registry)
    segments = [canonical_segment(segment) for segment in parts[1:]]
    return {
        "address": address,
        "root_id": root["id"],
        "type": root["type"],
        "code": root["code"],
        "category": root["category"],
        "segments": segments,
        "nested": bool(segments),
    }


def encode_path(raw_path: str) -> str:
    if not raw_path or raw_path != raw_path.strip():
        raise AddressError("path must be non-empty and have no surrounding whitespace")
    if raw_path.startswith("/") or raw_path.endswith("/"):
        raise AddressError("encode a relative path without leading or trailing /")
    encoded_segments = []
    for raw_segment in raw_path.split("/"):
        if not raw_segment:
            raise AddressError("path contains an empty segment")
        if raw_segment in {".", ".."}:
            raise AddressError("path must not contain . or .. segments")
        encoded_segments.append(
            quote(raw_segment, safe=PATH_SAFE, encoding="utf-8", errors="strict")
        )
    return "/".join(encoded_segments)


def command_new(args: argparse.Namespace, registry: dict) -> int:
    validate_type(args.resource_type, registry)
    for _ in range(args.count):
        print(f"{args.resource_type}_{secrets.token_hex(6)}")
    return 0


def command_validate(args: argparse.Namespace, registry: dict) -> int:
    _print(parse_address(args.address, registry))
    return 0


def command_encode_path(args: argparse.Namespace, registry: dict) -> int:
    del registry
    print(encode_path(args.path))
    return 0


def command_list_types(args: argparse.Namespace, registry: dict) -> int:
    del args
    for resource_type, metadata in sorted(registered_types(registry).items()):
        print(
            f"{resource_type}\t{metadata.get('category', '')}\t"
            f"{metadata.get('description', '')}"
        )
    return 0


def command_scan_repo(args: argparse.Namespace, registry: dict) -> int:
    del registry
    repo_backend, article_backend, _paper_resources, _search_backend = _portal_backends()
    article_roots, skipped_articles = article_backend.discover_article_roots(
        args.roots or None,
    )
    excluded_roots = {
        str(item["path"])
        for item in [*article_roots, *skipped_articles]
        if item.get("path")
    }
    for record in article_backend.load_article_records():
        excluded_roots.update(
            str(root["path"])
            for root in record.get("roots") or []
            if isinstance(root, dict) and root.get("path")
        )
    _print(
        repo_backend.scan_and_sync_repo_registry(
            args.roots or None,
            excluded_roots=sorted(excluded_roots),
            apply=args.apply,
        )
    )
    return 0


def command_scan_article(args: argparse.Namespace, registry: dict) -> int:
    del registry
    _repo_backend, article_backend, _paper_resources, _search_backend = _portal_backends()
    _print(
        article_backend.scan_and_sync_article_registry(
            args.roots or None,
            apply=args.apply,
        )
    )
    return 0


RESIDENT_ID_RE = re.compile(r"^resident_[0-9a-f]{12}$")

TODO_ID_RE = re.compile(r"^todo_[0-9a-f]{12}$")

LEGACY_RESIDENT_ADDRESS_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:(?:resident_agent|Agent_Team)_[0-9a-f]{6,12}|(?:agent|fork|team)_[0-9a-f]{10,12})(?![0-9a-f])"
)

DEFAULT_PROJECTS_ROOT = None
DEFAULT_PAPER_ROOT = None




DEFAULT_SKILL_ROOTS = ()


def _add_violation(
    output: list[dict[str, str]],
    kind: str,
    path: Path,
    field: str,
    value: object,
) -> None:
    output.append({
        "kind": kind,
        "path": str(path),
        "field": field,
        "value": str(value or "")[:240],
    })


def command_scan_resident(args: argparse.Namespace, registry: dict) -> int:
    """Audit Lineage and Resident facts against ResourceService descriptors."""

    del args, registry

    portal_root = str(DEFAULT_PORTAL_ROOT)
    if portal_root not in sys.path:
        sys.path.insert(0, portal_root)
    from management_portal.society_service import lineage_catalog, resident_catalog
    from management_portal.resources.collaboration_registry import (
        CollaborationDescriptorStore,
    )

    violations: list[dict[str, str]] = []

    def load(label: str, path: Path | str, reader):
        try:
            return reader()
        except (OSError, ValueError) as exc:
            _add_violation(
                violations,
                f"invalid_{label}_facts",
                path,
                label,
                exc,
            )
            return []

    lineages = load(
        "lineage",
        "PostgreSQL:lineages",
        lambda: lineage_catalog.get_catalog().snapshot(),
    )
    residents = load(
        "resident",
        "PostgreSQL:residents",
        lambda: resident_catalog.get_catalog().snapshot(),
    )
    descriptor_store = CollaborationDescriptorStore()
    descriptors = []
    for resource_type in sorted(COLLABORATION_TYPES):
        descriptors.extend(
            load(
                f"{resource_type}_descriptor",
                f"registry:{resource_type}",
                lambda resource_type=resource_type: descriptor_store.list(
                    resource_type
                ),
            )
        )
    lineage_ids = {
        str(item.get("lineage_id") or "").strip()
        for item in lineages
        if isinstance(item, dict)
    }
    resident_ids = {
        str(item.get("resident_id") or "").strip()
        for item in residents
        if isinstance(item, dict)
    }
    facts = lineage_ids | resident_ids
    descriptor_by_id = {
        str(item.get("resource_id") or "").strip(): item
        for item in descriptors
        if isinstance(item, dict) and str(item.get("resource_id") or "").strip()
    }
    for resource_id in sorted(facts):
        if resource_id not in descriptor_by_id:
            _add_violation(
                violations,
                "missing_resource_descriptor",
                f"registry:{resource_id.partition('_')[0]}",
                "resource_id",
                resource_id,
            )

    for resource_id, descriptor in sorted(descriptor_by_id.items()):
        resource_type = resource_id.partition("_")[0]
        if (
            resource_type in {"resident", "lineage"}
            and str(descriptor.get("status") or "active") != "deleted"
            and resource_id not in facts
        ):
            _add_violation(
                violations,
                "orphan_resource_descriptor",
                f"registry:{resource_type}/{resource_id}",
                "resource_id",
                resource_id,
            )
    for resident in residents:
        if not isinstance(resident, dict):
            continue
        resident_id = str(resident.get("resident_id") or "").strip()
        lineage_id = str(resident.get("lineage_id") or "").strip()
        root_resident_id = str(
            resident.get("root_resident_id") or resident_id
        ).strip()
        resident_path = f"PostgreSQL:residents/{resident_id}"
        if lineage_id not in lineage_ids:
            _add_violation(
                violations,
                "resident_lineage_missing",
                resident_path,
                "lineage_id",
                lineage_id,
            )
        if root_resident_id not in resident_ids:
            _add_violation(
                violations,
                "resident_root_missing",
                resident_path,
                "root_resident_id",
                root_resident_id,
            )

    violation_counts = {
        kind: sum(1 for item in violations if item["kind"] == kind)
        for kind in sorted({item["kind"] for item in violations})
    }
    _print(
        {
            "schema": "tfi.lineage_resource_audit.v1",
            "state": "clean" if not violations else "violations",
            "fact_store": "PostgreSQL SocietyService catalogs",
            "resource_registry": "PostgreSQL resource_registry",
            "lineage_count": len(lineage_ids),
            "resident_count": len(resident_ids),
            "descriptor_count": len(descriptor_by_id),
            "violation_count": len(violations),
            "violation_counts": violation_counts,
            "violations": violations[:200],
        }
    )
    return 0 if not violations else 2


def command_scan_paper(args: argparse.Namespace, registry: dict) -> int:
    del registry
    _repo_backend, _article_backend, paper_resources, _search_backend = _portal_backends()
    _print(
        paper_resources.scan_and_clean_paper_ids(
            paper_root=args.paper_root,
            apply=args.apply,
        )
    )
    return 0


def command_scan_search(args: argparse.Namespace, registry: dict) -> int:
    del registry
    (
        _repo_backend,
        _article_backend,
        _paper_resources,
        search_backend,
    ) = _portal_backends()
    _print(search_backend.scan_search_ids(apply=args.apply))
    return 0


def command_scan_knowledge(args: argparse.Namespace, registry: dict) -> int:
    del registry
    if not (DEFAULT_PORTAL_ROOT / "knowledge_board_lan").is_dir():
        raise RuntimeError(f"TFI Portal source not found: {DEFAULT_PORTAL_ROOT}")
    portal_root = str(DEFAULT_PORTAL_ROOT)
    if portal_root not in sys.path:
        sys.path.insert(0, portal_root)
    from knowledge_board_lan import knowledge_backend

    _print(knowledge_backend.scan_knowledge_ids(apply=args.apply))
    return 0


def _yaml_frontmatter(path: Path) -> dict:
    global yaml
    if yaml is None:
        import yaml
    text = path.read_text(encoding="utf-8", errors="strict").lstrip("\ufeff")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("YAML frontmatter required")
    end = next(
        (index for index, line in enumerate(lines[1:401], start=1) if line.strip() == "---"),
        None,
    )
    if end is None:
        raise ValueError("unterminated YAML frontmatter")
    payload = yaml.safe_load("\n".join(lines[1:end])) or {}
    if not isinstance(payload, dict):
        raise ValueError("YAML frontmatter must contain an object")
    return payload


def _project_review_paths(projects_root: Path) -> list[tuple[Path, bool]]:
    paths: list[tuple[Path, bool]] = []
    if not projects_root.is_dir():
        return paths
    for review_root in sorted(projects_root.glob("*/knowledge_base/review")):
        if not review_root.is_dir():
            continue
        paths.extend((path, False) for path in sorted(review_root.glob("*.md")))
        archive_root = review_root / ".archive"
        if archive_root.is_dir():
            paths.extend((path, True) for path in sorted(archive_root.glob("*.md")))
    return paths


def _paper_review_ids(paper_root: Path, registry: dict) -> set[str]:
    review_ids: set[str] = set()
    if not paper_root.is_dir():
        return review_ids
    for path in paper_root.glob("items/*/*/reviews/*.md"):
        try:
            frontmatter = _yaml_frontmatter(path)
        except (OSError, UnicodeError, ValueError, yaml.YAMLError):
            continue
        review_id = str(frontmatter.get("review_id") or "").strip()
        try:
            parsed = parse_root(review_id, registry)
        except ValueError:
            continue
        if parsed["type"] == "review":
            review_ids.add(review_id)
    return review_ids


def _rewrite_frontmatter_kb_id(path: Path, kb_id: str) -> None:
    text = path.read_text(encoding="utf-8", errors="strict")
    match = re.match(
        r"(?P<start>\ufeff?---\r?\n)(?P<header>.*?)(?P<end>\r?\n---(?:\r?\n|$))",
        text,
        flags=re.DOTALL,
    )
    if match is None:
        raise ValueError(f"YAML frontmatter required: {path}")
    header = match.group("header")
    identity_lines = re.compile(r"(?m)^(?:id|kb_id)\s*:.*(?:\n|$)")
    header = identity_lines.sub("", header).lstrip("\r\n")
    header = f"kb_id: {kb_id}\n{header}"
    updated = (
        match.group("start")
        + header
        + match.group("end")
        + text[match.end() :]
    )
    try:
        mode = path.stat().st_mode & 0o7777
    except OSError:
        mode = 0o664
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    try:
        temporary.write_text(updated, encoding="utf-8")
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _project_review_id_plan(
    projects_root: Path,
    paper_root: Path,
    registry: dict,
) -> dict[str, object]:
    records: list[dict[str, object]] = []
    blockers: list[dict[str, str]] = []
    owners: dict[str, Path] = {
        review_id: paper_root for review_id in _paper_review_ids(paper_root, registry)
    }
    used_ids = set(owners)
    duplicate_count = 0
    for path, archived in _project_review_paths(projects_root):
        try:
            frontmatter = _yaml_frontmatter(path)
        except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
            blockers.append(
                {
                    "path": str(path),
                    "error": str(exc),
                }
            )
            continue
        current_id = str(frontmatter.get("kb_id") or "").strip()
        canonical = False
        try:
            parsed = parse_root(current_id, registry)
            canonical = parsed["type"] == "review"
        except ValueError:
            canonical = False
        duplicate = bool(canonical and current_id in owners)
        if duplicate:
            duplicate_count += 1
        if canonical and not duplicate:
            owners[current_id] = path
            used_ids.add(current_id)
        records.append(
            {
                "path": path,
                "archived": archived,
                "current_id": current_id,
                "canonical": canonical,
                "duplicate": duplicate,
            }
        )

    assignments: list[dict[str, str]] = []
    for record in records:
        if record["canonical"] and not record["duplicate"]:
            continue
        next_id = ""
        for _attempt in range(100):
            candidate = f"review_{secrets.token_hex(6)}"
            if candidate not in used_ids:
                next_id = candidate
                used_ids.add(candidate)
                break
        if not next_id:
            raise RuntimeError("could not allocate a unique Review ID")
        assignments.append(
            {
                "path": str(record["path"]),
                "previous_id": str(record["current_id"]),
                "review_id": next_id,
                "reason": "duplicate" if record["duplicate"] else "invalid",
            }
        )
    return {
        "records": records,
        "assignments": assignments,
        "blockers": blockers,
        "duplicate_count": duplicate_count,
        "paper_review_ids_reserved": len(_paper_review_ids(paper_root, registry)),
    }


def scan_project_review_ids(
    *,
    projects_root: str | Path | None = None,
    paper_root: str | Path | None = None,
    registry: dict,
    apply: bool = False,
) -> dict[str, object]:
    if DEFAULT_PROJECTS_ROOT is None:
        _configure_portal()
    projects_path = Path(projects_root if projects_root is not None else DEFAULT_PROJECTS_ROOT).expanduser()
    papers_path = Path(paper_root if paper_root is not None else DEFAULT_PAPER_ROOT).expanduser()
    plan = _project_review_id_plan(projects_path, papers_path, registry)
    blockers = list(plan["blockers"])
    if apply and blockers:
        raise ValueError(
            f"Review ID update blocked by {len(blockers)} unreadable document(s)"
        )
    if apply:
        for assignment in plan["assignments"]:
            _rewrite_frontmatter_kb_id(
                Path(assignment["path"]),
                assignment["review_id"],
            )
    records = list(plan["records"])
    return {
        "schema": "tfi.review_identity_scan.v1",
        "apply": bool(apply),
        "review_count": len(records),
        "active_count": sum(not bool(record["archived"]) for record in records),
        "archived_count": sum(bool(record["archived"]) for record in records),
        "paper_review_ids_reserved": plan["paper_review_ids_reserved"],
        "changed_count": len(plan["assignments"]),
        "unchanged_count": len(records) - len(plan["assignments"]),
        "invalid_count": sum(
            assignment["reason"] == "invalid"
            for assignment in plan["assignments"]
        ),
        "duplicate_count": plan["duplicate_count"],
        "blocker_count": len(blockers),
        "blockers": blockers,
        "assignments": plan["assignments"],
    }


def command_scan_review(args: argparse.Namespace, registry: dict) -> int:
    report = scan_project_review_ids(
        projects_root=args.projects_root,
        paper_root=args.paper_root,
        registry=registry,
        apply=args.apply,
    )
    _print(report)
    return 0 if not report["blocker_count"] else 2


def _system_skill_paths(skill_roots: list[Path], projects_root: Path) -> list[Path]:
    paths: set[Path] = set()
    for root in skill_roots:
        if root.is_file() and root.name == "SKILL.md":
            paths.add(root.resolve(strict=False))
        elif root.is_dir():
            paths.update(path.resolve(strict=False) for path in root.rglob("SKILL.md"))
    if projects_root.is_dir():
        paths.update(
            path.resolve(strict=False)
            for path in projects_root.glob("*/knowledge_base/skill/**/SKILL.md")
        )
    return sorted(paths)


def _audit_system_skill_ids(
    skill_roots: list[Path],
    projects_root: Path,
    registry: dict,
) -> tuple[int, list[dict[str, str]]]:
    violations: list[dict[str, str]] = []
    owners: dict[str, Path] = {}
    paths = _system_skill_paths(skill_roots, projects_root)
    for path in paths:
        try:
            frontmatter = _yaml_frontmatter(path)
        except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
            _add_violation(violations, "invalid_skill_frontmatter", path, "frontmatter", exc)
            continue
        metadata = frontmatter.get("metadata")
        metadata_id = (
            str(metadata.get("id") or "").strip()
            if isinstance(metadata, dict)
            else ""
        )
        document_id = str(frontmatter.get("id") or "").strip()
        skill_id = metadata_id or document_id
        id_field = "metadata.id" if metadata_id else "id"
        if metadata_id and document_id and metadata_id != document_id:
            _add_violation(
                violations,
                "conflicting_skill_id",
                path,
                "id",
                f"{document_id} != {metadata_id}",
            )
            continue
        try:
            parsed = parse_root(skill_id, registry)
            if parsed["type"] != "skill":
                raise AddressError("metadata.id must use skill_*")
        except ValueError as exc:
            _add_violation(violations, "invalid_skill_id", path, id_field, skill_id or exc)
            continue
        previous = owners.get(skill_id)
        if previous is not None:
            _add_violation(
                violations,
                "duplicate_skill_id",
                path,
                id_field,
                f"{skill_id} (also {previous})",
            )
        else:
            owners[skill_id] = path
    return len(paths), violations


def _audit_system_todo_ids(
    projects_root: Path,
    registry: dict,
) -> tuple[int, dict[str, Path], list[dict[str, str]]]:
    violations: list[dict[str, str]] = []
    owners: dict[str, Path] = {}
    paths = (
        sorted(projects_root.glob("*/knowledge_base/todo/*.md"))
        if projects_root.is_dir()
        else []
    )
    for path in paths:
        try:
            frontmatter = _yaml_frontmatter(path)
        except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
            _add_violation(violations, "invalid_todo_frontmatter", path, "frontmatter", exc)
            continue
        todo_id = str(frontmatter.get("id") or "").strip()
        try:
            parsed = parse_root(todo_id, registry)
            if parsed["type"] != "todo":
                raise AddressError("frontmatter id must use todo_*")
        except ValueError as exc:
            _add_violation(violations, "invalid_todo_id", path, "id", todo_id or exc)
            continue
        previous = owners.get(todo_id)
        if previous is not None:
            _add_violation(
                violations,
                "duplicate_todo_id",
                path,
                "id",
                f"{todo_id} (also {previous})",
            )
        else:
            owners[todo_id] = path
    return len(paths), owners, violations




def _audit_portal_resource_index() -> tuple[dict[str, int], list[dict[str, str]]]:
    portal_root = str(DEFAULT_PORTAL_ROOT)
    if portal_root not in sys.path:
        sys.path.insert(0, portal_root)
    from management_portal import resource_index_backend

    store = resource_index_backend.IndexStore()
    counts: dict[str, int] = {}
    violations: list[dict[str, str]] = []
    for adapter_type in resource_index_backend.DEFAULT_ADAPTERS:
        adapter = adapter_type()
        try:
            records, source_state = adapter.scan()
            store.publish_snapshot(
                adapter.name,
                records,
                source_state,
                persist=False,
            )
            counts[adapter.name] = len(records)
        except Exception as exc:
            _add_violation(
                violations,
                "resource_index_adapter_error",
                DEFAULT_PORTAL_ROOT,
                adapter.name,
                exc,
            )
    return counts, violations


def command_scan_system(args: argparse.Namespace, registry: dict) -> int:
    projects_root = Path(args.projects_root).expanduser()
    skill_roots = [Path(path).expanduser() for path in (args.skill_root or DEFAULT_SKILL_ROOTS)]
    skill_count, skill_violations = _audit_system_skill_ids(
        skill_roots,
        projects_root,
        registry,
    )
    todo_count, _todo_paths, todo_violations = _audit_system_todo_ids(
        projects_root,
        registry,
    )
    review_report = scan_project_review_ids(
        projects_root=projects_root,
        paper_root=args.paper_root,
        registry=registry,
    )
    review_violations: list[dict[str, str]] = []
    for blocker in review_report["blockers"]:
        _add_violation(
            review_violations,
            "invalid_review_frontmatter",
            Path(blocker["path"]),
            "frontmatter",
            blocker["error"],
        )
    for assignment in review_report["assignments"]:
        _add_violation(
            review_violations,
            f"{assignment['reason']}_review_id",
            Path(assignment["path"]),
            "id",
            assignment["previous_id"],
        )
    if args.skip_index:
        adapter_counts: dict[str, int] = {}
        index_violations: list[dict[str, str]] = []
    else:
        adapter_counts, index_violations = _audit_portal_resource_index()
    violations = [
        *skill_violations,
        *todo_violations,
        *review_violations,
        *index_violations,
    ]
    violation_counts = {
        kind: sum(1 for item in violations if item.get("kind") == kind)
        for kind in sorted(
            {
                str(item.get("kind") or "")
                for item in violations
                if item.get("kind")
            }
        )
    }
    violation_samples = {
        kind: next(
            item for item in violations if item.get("kind") == kind
        )
        for kind in violation_counts
    }
    report = {
        "schema": "tfi.system_resource_id_audit.v1",
        "state": "clean" if not violations else "violations",
        "adapter_counts": adapter_counts,
        "resource_count": sum(adapter_counts.values()),
        "skill_documents_scanned": skill_count,
        "todo_documents_scanned": todo_count,
        "review_documents_scanned": review_report["review_count"],
        "violation_count": len(violations),
        "violation_counts": violation_counts,
        "violation_samples": violation_samples,
        "violations": violations[:200],
    }
    _print(report)
    return 0 if not violations else 2


def command_register_repo(args: argparse.Namespace, registry: dict) -> int:
    del registry
    repo_backend, article_backend, _paper_resources, _search_backend = _portal_backends()
    if article_backend.is_article_storage_path(args.path):
        raise AddressError(
            "Article storage uses article_* and cannot be registered as repo_*"
        )
    _print(
        repo_backend.register_repo_checkout(
            args.path,
            project_ids=args.project_id,
            repo_id=args.repo_id,
        )
    )
    return 0


def command_register_article(args: argparse.Namespace, registry: dict) -> int:
    del registry
    _repo_backend, article_backend, _paper_resources, _search_backend = _portal_backends()
    _print(
        article_backend.register_article(
            args.path,
            project_ids=args.project_id,
            article_id=args.article_id,
        )
    )
    return 0


def command_resolve(args: argparse.Namespace, registry: dict) -> int:
    parse_address(args.address, registry)
    if args.paper_root:
        raise AddressError("resolve uses the Portal resource service; --paper-root is not accepted")
    result = _portal_resource_service().resolve(
        args.address,
        actor=args.actor,
    )
    _print(result)
    return 0


def command_list(args: argparse.Namespace, registry: dict) -> int:
    validate_type(args.resource_type, registry)
    if args.resource_type == "device":
        _print(_device_backend().load_device_records())
        return 0
    repo_backend, article_backend, paper_resources, search_backend = _portal_backends()
    if args.resource_type == "repo":
        result = repo_backend.load_repo_records()
    elif args.resource_type == "article":
        result = article_backend.load_article_records()
    elif args.resource_type == "paper":
        result = paper_resources.load_paper_resources(args.paper_root)
    elif args.resource_type == "search":
        result = search_backend.load_search_resources(
            actor=args.actor,
            include_deleted=args.include_deleted,
        )
    elif args.resource_type in COLLABORATION_TYPES:
        statuses = (
            ["active", "creating", "running", "budded", "deleted"]
            if args.include_deleted
            else []
        )
        result = _portal_resource_service().query(
            actor=args.actor,
            resource_types=[args.resource_type],
            statuses=statuses,
            limit=5000,
            include_attributes=True,
        )["resources"]
    else:
        raise AddressError(f"no registry is configured for {args.resource_type}_* resources")
    _print(result)
    return 0


def command_context(args: argparse.Namespace, registry: dict) -> int:
    parsed = parse_address(args.address, registry)
    if parsed["type"] != "resident" or parsed["nested"]:
        raise AddressError("context requires a bare resident_* address")
    if args.relative_to:
        relative = parse_address(args.relative_to, registry)
        if relative["type"] != "resident" or relative["nested"]:
            raise AddressError("--relative-to requires a bare resident_* address")
    _print(
        _portal_society_service().projection.read_context(
            args.address,
            actor=args.actor,
            relative_to_address=args.relative_to,
            max_messages=args.max_messages,
            max_chars=args.max_chars,
        )
    )
    return 0



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create, register, scan, and resolve TFI resource IDs."
    )
    parser.add_argument("--actor", dest="registry_actor", default="", help="读取类型目录的 Portal 用户（Resident 使用自身凭证）")
    parser.add_argument("--resident-id", default=None, help="当前 Resident 的稳定 ID；运行环境已注入时省略")
    commands = parser.add_subparsers(dest="command", required=True)

    new_parser = commands.add_parser("new", help="generate one or more root IDs")
    new_parser.add_argument("resource_type", help="registered resource type")
    new_parser.add_argument("--count", type=int, default=1, choices=range(1, 101))
    new_parser.set_defaults(handler=command_new)

    validate_parser = commands.add_parser("validate", help="validate and parse an address")
    validate_parser.add_argument("address")
    validate_parser.set_defaults(handler=command_validate)

    encode_parser = commands.add_parser(
        "encode-path", help="percent-encode a relative nested-address path"
    )
    encode_parser.add_argument("path")
    encode_parser.set_defaults(handler=command_encode_path)

    list_types_parser = commands.add_parser("list-types", help="list approved resource types")
    list_types_parser.set_defaults(handler=command_list_types)

    scan_parser = commands.add_parser("scan", help="scan and reconcile a resource registry")
    scan_types = scan_parser.add_subparsers(dest="scan_type", required=True)

    scan_repo = scan_types.add_parser("repo", help="scan Git checkouts")
    scan_repo.add_argument("roots", nargs="*", help="defaults to configured shared storage and Portal")
    scan_repo.add_argument("--apply", action="store_true")
    scan_repo.set_defaults(handler=command_scan_repo)

    scan_article = scan_types.add_parser("article", help="scan Article or Project roots")
    scan_article.add_argument("roots", nargs="*", help="defaults to registered Projects")
    scan_article.add_argument("--apply", action="store_true")
    scan_article.set_defaults(handler=command_scan_article)

    scan_paper = scan_types.add_parser("paper", help="scan Portal Paper manifests")
    scan_paper.add_argument("--apply", action="store_true")
    scan_paper.add_argument("--paper-root", default=None)
    scan_paper.set_defaults(handler=command_scan_paper)

    scan_search = scan_types.add_parser(
        "search", help="scan Paper and Knowledge semantic searches"
    )
    scan_search.add_argument("--apply", action="store_true")
    scan_search.set_defaults(handler=command_scan_search)

    scan_knowledge = scan_types.add_parser(
        "knowledge", help="scan Knowledge and Skill entry IDs"
    )
    scan_knowledge.add_argument("--apply", action="store_true")
    scan_knowledge.set_defaults(handler=command_scan_knowledge)

    scan_review = scan_types.add_parser(
        "review", help="scan active and archived Project Review IDs"
    )
    scan_review.add_argument("--apply", action="store_true")
    scan_review.add_argument("--projects-root", default=None)
    scan_review.add_argument("--paper-root", default=None)
    scan_review.set_defaults(handler=command_scan_review)

    scan_resident = scan_types.add_parser(
        "resident",
        help="audit SocietyService facts against ResourceService records",
    )
    scan_resident.set_defaults(handler=command_scan_resident)

    scan_system = scan_types.add_parser(
        "system",
        help="audit all Portal-indexed resources plus Skill, TODO, and Review metadata",
    )
    scan_system.add_argument("--projects-root", default=None)
    scan_system.add_argument("--paper-root", default=None)
    scan_system.add_argument("--skill-root", action="append", default=[])
    scan_system.add_argument(
        "--skip-index",
        action="store_true",
        help="audit Skill and TODO metadata without rebuilding the Portal resource index",
    )
    scan_system.set_defaults(handler=command_scan_system)

    register_parser = commands.add_parser("register", help="register one logical resource")
    register_types = register_parser.add_subparsers(dest="register_type", required=True)

    register_repo = register_types.add_parser("repo", help="register one Git checkout")
    register_repo.add_argument("path")
    register_repo.add_argument("--project-id", action="append", default=[])
    register_repo.add_argument("--repo-id", default="")
    register_repo.set_defaults(handler=command_register_repo)

    register_article = register_types.add_parser("article", help="register one Article root")
    register_article.add_argument("path")
    register_article.add_argument("--project-id", action="append", default=[])
    register_article.add_argument("--article-id", default="")
    register_article.set_defaults(handler=command_register_article)

    resolve_parser = commands.add_parser(
        "resolve", help="resolve an address; its prefix selects the registry"
    )
    resolve_parser.add_argument("address")
    resolve_parser.add_argument("--actor", default="")
    resolve_parser.add_argument("--paper-root", default=None)
    resolve_parser.set_defaults(handler=command_resolve)

    list_parser = commands.add_parser("list", help="list one registered resource type")
    list_parser.add_argument("resource_type")
    list_parser.add_argument("--paper-root", default=None)
    list_parser.add_argument("--actor", default="")
    list_parser.add_argument("--include-deleted", action="store_true")
    list_parser.set_defaults(handler=command_list)

    context_parser = commands.add_parser("context", help="read a Resident conversation")
    context_parser.add_argument("address", help="source resident_* ID")
    context_parser.add_argument("--relative-to", default="")
    context_parser.add_argument("--actor", required=True)
    context_parser.add_argument(
        "--max-messages", type=int, default=DEFAULT_CONTEXT_MAX_MESSAGES
    )
    context_parser.add_argument("--max-chars", type=int, default=DEFAULT_CONTEXT_MAX_CHARS)
    context_parser.set_defaults(handler=command_context)
    return parser


def _use_portal_python() -> None:
    """Re-run under the Portal interpreter when this one lacks the database driver."""
    try:
        import psycopg  # noqa: F401
    except ModuleNotFoundError:
        if PORTAL_PYTHON.is_file() and not read_product_env("PORTAL_RESOURCE_ID_REEXEC"):
            mirror_product_env(os.environ, "PORTAL_RESOURCE_ID_REEXEC", "1")
            os.execv(str(PORTAL_PYTHON), [str(PORTAL_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])


def main() -> int:
    global yaml
    parser = build_parser()
    args = parser.parse_args()
    try:
        _configure_portal()
        _use_portal_python()
        import yaml
        if hasattr(args, "projects_root") and args.projects_root is None:
            args.projects_root = str(DEFAULT_PROJECTS_ROOT)
        if hasattr(args, "paper_root") and args.paper_root is None and hasattr(args, "projects_root"):
            args.paper_root = str(DEFAULT_PAPER_ROOT)
        registry = load_registry(getattr(args, "actor", "") or args.registry_actor, args.resident_id)
        return args.handler(args, registry)
    except (RuntimeError, OSError, json.JSONDecodeError, KeyError, ValueError, ImportError) as exc:
        _print({"error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
