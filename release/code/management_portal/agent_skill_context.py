from __future__ import annotations

from management_common.portal_paths import path as portal_path

import hashlib
import threading
from dataclasses import dataclass, replace
from pathlib import Path


PORTAL_SYSTEM_SKILL = "tfi-agent-rule"
PORTAL_ROLE_FILES = {
    "development": "portal-development.md",
    "resident": "portal-resident.md",
}
DEFAULT_MATERIALIZED_SKILL_CHAR_BUDGET = 450_000


@dataclass(frozen=True)
class MaterializedSkillSource:
    path: str
    sha256: str
    content: str


@dataclass(frozen=True)
class MaterializedPortalSkills:
    role: str
    content: str
    sources: tuple[MaterializedSkillSource, ...] = ()
    status: str = "fresh"
    reason: str = ""


@dataclass(frozen=True)
class _CachedSkillFile:
    mtime_ns: int
    size: int
    source: MaterializedSkillSource


_cache_lock = threading.RLock()
_file_cache: dict[str, _CachedSkillFile] = {}
_last_good_by_role: dict[tuple[str, str], MaterializedPortalSkills] = {}


def _read_hashed_source(path: Path) -> MaterializedSkillSource:
    clean_path = str(path)
    stat = path.stat()
    with _cache_lock:
        cached = _file_cache.get(clean_path)
        if (
            cached is not None
            and cached.mtime_ns == stat.st_mtime_ns
            and cached.size == stat.st_size
        ):
            return cached.source
    content = path.read_text(encoding="utf-8")
    source = MaterializedSkillSource(
        path=clean_path,
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        content=content,
    )
    with _cache_lock:
        _file_cache[clean_path] = _CachedSkillFile(
            mtime_ns=stat.st_mtime_ns,
            size=stat.st_size,
            source=source,
        )
    return source


def _deduplicate_sources(
    sources: tuple[MaterializedSkillSource, ...],
) -> tuple[MaterializedSkillSource, ...]:
    seen_hashes: set[str] = set()
    retained: list[MaterializedSkillSource] = []
    for source in sources:
        if source.sha256 in seen_hashes:
            continue
        seen_hashes.add(source.sha256)
        retained.append(source)
    return tuple(retained)


def _render_materialized_sources(
    *,
    role: str,
    sources: tuple[MaterializedSkillSource, ...],
    status: str,
    reason: str,
) -> str:
    lines = [
        (
            '<portal-materialized-skill-set '
            f'role="{role}" status="{status}">'
        ),
        (
            "Portal 已在服务端读取并按 SHA-256 去重以下核心规则。"
            "这些正文就是本 session 的必读 Skill；不要再通过工具重复读取这些路径。"
        ),
    ]
    if reason:
        lines.append(f"物化状态说明：{reason}")
    for source in sources:
        lines.extend([
            (
                '<portal-materialized-skill '
                f'path="{source.path}" sha256="{source.sha256}">'
            ),
            source.content.rstrip(),
            "</portal-materialized-skill>",
        ])
    lines.append("</portal-materialized-skill-set>")
    return "\n".join(lines)


def _cached_materialization(
    stale: MaterializedPortalSkills,
    *,
    reason: str,
    char_budget: int,
) -> MaterializedPortalSkills:
    content = _render_materialized_sources(
        role=stale.role,
        sources=stale.sources,
        status="stale",
        reason=reason,
    )
    if len(content) > max(1, int(char_budget)):
        raise ValueError("Portal core Skill content exceeds the configured budget")
    return replace(stale, status="stale", reason=reason, content=content)


def materialize_portal_skills(
    role: str,
    *,
    skill_root: str = str(portal_path("share_root") / "Skills"),
    char_budget: int = DEFAULT_MATERIALIZED_SKILL_CHAR_BUDGET,
    system_skill_name: str | None = None,
) -> MaterializedPortalSkills:
    """Resolve the stable Portal rule pair once, with a fail-soft cache.

    The cache is content-addressed for prompt provenance and metadata-addressed
    for cheap reuse.  If the source is temporarily unavailable, the last known
    materialization is used and labelled stale.
    Distribution may explicitly name the canonical directory; only skill_root
    selects an installation-specific location.
    """

    role = str(role or "").strip()
    if role not in {"general", *PORTAL_ROLE_FILES}:
        raise ValueError("unknown Portal agent role")
    name = PORTAL_SYSTEM_SKILL if system_skill_name is None else system_skill_name
    if name != PORTAL_SYSTEM_SKILL:
        raise ValueError("Portal system Skill directory must be tfi-agent-rule")
    root = Path(skill_root) / name
    cache_key = (str(root), role)
    paths = [root / "SKILL.md"]
    role_filename = PORTAL_ROLE_FILES.get(role)
    if role_filename:
        paths.append(root / role_filename)
    try:
        sources = _deduplicate_sources(tuple(
            _read_hashed_source(path) for path in paths
        ))
    except (OSError, UnicodeError):
        with _cache_lock:
            stale = _last_good_by_role.get(cache_key)
        if stale is not None:
            reason = "source_temporarily_unavailable"
            return _cached_materialization(
                stale,
                reason=reason,
                char_budget=char_budget,
            )
        raise RuntimeError("Portal core Skill source is unavailable")

    provisional = _render_materialized_sources(
        role=role,
        sources=sources,
        status="fresh",
        reason="",
    )
    if len(provisional) > max(1, int(char_budget)):
        with _cache_lock:
            stale = _last_good_by_role.get(cache_key)
        if stale is not None:
            return _cached_materialization(
                stale,
                reason="current_sources_over_soft_budget",
                char_budget=char_budget,
            )
        raise ValueError("Portal core Skill content exceeds the configured budget")

    result = MaterializedPortalSkills(
        role=role,
        content=provisional,
        sources=sources,
        status="fresh",
        reason="",
    )
    with _cache_lock:
        _last_good_by_role[cache_key] = result
    return result
