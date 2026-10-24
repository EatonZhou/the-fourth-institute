"""Backend data helpers for the Paper research workspace."""

from __future__ import annotations
from management_common.format_tags import schema_matches
from management_common.portal_paths import path as portal_path

from management_common.principal import is_user_or_resident
from management_common.scan_cancellation import check_scan_cancelled, walk_paths

from management_portal import paper_favorites, paper_state_store

import json
import logging
import math
import hashlib
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from contextlib import contextmanager
from management_common.file_lock import lock_stream
from management_common.secure_paths import relative_parts
import html as html_lib
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Pattern
from urllib.parse import quote, unquote, urlencode, urlparse

from management_portal.resources.portal import portal_url_for_address

from PIL import Image, ImageDraw
from management_common.limit_contract import normalize_optional_limit
from management_common import process_runtime
from management_portal import semantic_search_jobs
from management_portal.notifications import publish_local_projection_change
from management_portal.resources import get_resource_service
from management_portal.shared_state import atomic_replace_json, exclusive_file_lock


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.getenv("PAPER_WORKSPACE_ROOT", str(portal_path("staging_root") / "shared" / "portal_paper_workspace")))
PAPER_STORE_ROOT = Path(os.getenv("PAPER_STORE_ROOT", str(portal_path("share_root") / "Papers")))
# Reviews, notes, job records and curated venues live in paper_state_store.
PAPER_COLLECTOR_WORK_ROOT = PAPER_STORE_ROOT / "_work" / "collector"
PAPER_ASSETS_WORK_ROOT = PAPER_STORE_ROOT / "_work" / "assets"
PAPER_RUN_LOCK_ROOT = PAPER_STORE_ROOT / "_work" / "run_locks"
CCF_CATALOG_CACHE_PATH = PAPER_STORE_ROOT / "_index" / "ccf_catalog_v7.json"
CCF_DBLP_CACHE_ROOT = PAPER_STORE_ROOT / "_work" / "ccf_dblp"
CCF_ENRICHMENT_CACHE_ROOT = CCF_DBLP_CACHE_ROOT / "_enrichment"
CCF_REFRESH_REPORT_PATH = CCF_DBLP_CACHE_ROOT / "refresh-report.json"
CCF_CATALOG_URL = os.getenv("PAPER_CCF_CATALOG_URL", "https://ccf.atom.im/")
ANALYSIS_ROOT = DATA_ROOT / "analysis"
PDF_TEXT_ROOT = DATA_ROOT / "pdf_text"
PAPER_PDF_UPLOAD_MAX_BYTES = 512 * 1024 * 1024
PAPER_FIGURE_IMAGE_MAX_BYTES = 25 * 1024 * 1024
PAPER_FIGURE_IMAGE_MAX_PIXELS = 40_000_000
FIGURE_EXPLANATION_RESULT_SCHEMA = "tfi.paper.figure-explanation.result.v1"
PAPER_REGISTRATION_DRAFT_TTL_SECONDS = 24 * 60 * 60


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default)) or str(default)))
    except Exception:
        return max(minimum, int(default))


EXP_STALE_SECONDS = 45 * 60
# Legacy or unconfirmed jobs cannot be resumed from a canonical input receipt.
COLLECTOR_STALE_SECONDS = 18 * 60 * 60
FIGURE_DRAFT_STALE_SECONDS = _env_int("PAPER_FIGURE_DRAFT_STALE_SECONDS", 90 * 60, minimum=1)
LOCK = threading.RLock()
_DEEP_READ_TASK_LOCK = threading.RLock()
_ACTIVE_DEEP_READ_TASKS: set[str] = set()


def _notify_paper_projection(
    target_id: object,
    *,
    actors: object = None,
    fields: list[str] | None = None,
) -> None:
    target = str(target_id or "").strip()
    if not target:
        return
    publish_local_projection_change(
        "paper",
        target,
        actors=(
            [str(item).strip() for item in actors if str(item or "").strip()]
            if isinstance(actors, (list, tuple, set))
            else None
        ),
        fields=fields,
    )


NON_PROJECT_RELATION_IDS = {"general", "references", "auto collected", "auto-collected", "unsorted"}


def _is_project_relation_id(value: object) -> bool:
    text = str(value or "").strip()
    return bool(text) and text.casefold() not in NON_PROJECT_RELATION_IDS


def _is_non_project_tag(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    key = text.casefold()
    if key.startswith("project:"):
        key = key.split(":", 1)[1].strip()
    return key in NON_PROJECT_RELATION_IDS


def _project_id_from_tag(value: object) -> str:
    text = str(value or "").strip()
    if text.casefold().startswith("project:"):
        return text.split(":", 1)[1].strip()
    return text


def _is_project_tag(value: object, project_ids: set[str]) -> bool:
    return _project_id_from_tag(value) in project_ids

ALLOWED_FILE_ROOTS = [
    portal_path("share_root"),
    portal_path("staging_root"),
    DATA_ROOT,
    PAPER_STORE_ROOT,
    WORKSPACE_ROOT,
]

PAPER_FIELDS = {
    "id",
    "slug",
    "title",
    "authors",
    "venue",
    "year",
    "session",
    "folder",
    "abstract",
    "tags",
    "project_ids",
    "relations",
    "workload",
    "platform",
    "architecture",
    "optimization",
    "pdf_path",
    "analysis_path",
    "status",
    "created_by",
    "created_at",
    "updated_at",
}

WORKFLOW_STEPS = [
    {
        "id": "search",
        "title": "Paper deep search",
        "summary": "围绕 topic、venue 或已有 seed paper 收集候选论文，形成可复用的 paper manifest。",
    },
    {
        "id": "read",
        "title": "Paper deep read",
        "summary": "读取 PDF、figure/table 和 captions，产出结构化 analysis markdown。",
    },
    {
        "id": "library",
        "title": "Paper library",
        "summary": "把 PDF、analysis、metadata 和 tags 放进同一套检索入口。",
    },
    {
        "id": "assistant",
        "title": "Research chat",
        "summary": "用库内 metadata、analysis 和 PDF text 支持单篇或全库问答。",
    },
    {
        "id": "review",
        "title": "Multi-reviewer review",
        "summary": "按 novelty、method、evaluation 三个视角生成独立评审和 author action memo。",
    },
]

REVIEW_LENSES = [
    {
        "id": "A",
        "title": "Reviewer A",
        "subtitle": "Novelty & Contribution",
        "checks": ["related work", "real delta", "significance"],
    },
    {
        "id": "B",
        "title": "Reviewer B",
        "subtitle": "Method & Soundness",
        "checks": ["mechanism", "assumptions", "hidden cost"],
    },
    {
        "id": "C",
        "title": "Reviewer C",
        "subtitle": "Evaluation & Fairness",
        "checks": ["baselines", "metrics", "comparison"],
    },
]


def _now() -> float:
    return round(time.time(), 6)


def _validated_paper_id(paper_id: object) -> str:
    paper_id = str(paper_id or "").strip()
    try:
        get_resource_service().validate_id(paper_id, "paper")
    except ValueError:
        raise ValueError(f"invalid Paper ID: {paper_id or '<empty>'}")
    return paper_id


def _manifest_matches_paper_address(manifest: dict, address: str) -> bool:
    return str(manifest.get("paper_id") or "").strip() == address


def _coerce_timestamp(value, default: int | None = None) -> int:
    if default is None:
        default = _now()
    try:
        return int(value)
    except Exception:
        pass
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except Exception:
        return default


def _normalize_fingerprint_text(value: str) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _paper_authors_string(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())[:600]
    return str(value or "").strip()[:600]


def _paper_authors_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in re.split(r"[,;，、]\s*", str(value or "")) if item.strip()]


def _manifest_pdf_path(manifest: dict, pdf_file: Path | None) -> str:
    if pdf_file and pdf_file.is_file():
        return str(pdf_file)
    return ""


def _paper_source_pdf_url(paper: dict, existing_source: dict) -> str:
    explicit = str(paper.get("_source_pdf_url") or paper.get("source_pdf_url") or "").strip()
    if explicit:
        return explicit
    pdf_path = str(paper.get("pdf_path") or "").strip()
    if _is_url(pdf_path):
        return pdf_path
    return str(existing_source.get("pdf_url") or "").strip()


def _append_pdf_pending_note(abstract: str, pdf_url: str, error: str) -> str:
    parts = [str(abstract or "").strip()]
    if pdf_url:
        parts.append(f"PDF pending: {pdf_url}")
    if error:
        parts.append(f"PDF download error: {str(error)[:500]}")
    return "\n\n".join(part for part in parts if part)[:5000]


def _paper_bundle_path(root: Path, value: str | Path) -> Path | None:
    bundle_root = Path(root).expanduser().resolve(strict=False)
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = bundle_root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(bundle_root)
    except ValueError:
        return None
    return resolved


class PaperStore:
    def __init__(self, root: Path = PAPER_STORE_ROOT) -> None:
        self.root = Path(root)
        self.items_root = self.root / "items"
        self.index_root = self.root / "_index"

    def ensure(self) -> None:
        for path in (self.items_root, self.index_root, self.root / "_incoming", self.root / "_work", self.root / "_trash"):
            path.mkdir(parents=True, exist_ok=True)

    def index_path(self, name: str) -> Path:
        return self.index_root / name

    def manifest_paths(self) -> list[Path]:
        if not self.items_root.exists():
            return []
        manifests = []
        for year in self.items_root.iterdir():
            check_scan_cancelled()
            if year.is_dir():
                manifests.extend(path for path in walk_paths(year) if path.name == "manifest.json")
        return sorted(manifests)

    def read_manifest(self, path: Path) -> dict:
        data = _load_json(path, {})
        return data if isinstance(data, dict) else {}

    def manifest_map(self) -> dict[str, str]:
        data = _load_json(self.index_path("manifests.json"), {})
        return (
            {str(key): str(value) for key, value in data.items()}
            if isinstance(data, dict)
            else {}
        )

    def manifest_for_paper_id(self, paper_id: str) -> tuple[Path, dict] | None:
        try:
            target = _validated_paper_id(paper_id)
        except ValueError:
            return None
        relative_manifest = str(self.manifest_map().get(target) or "").strip()
        if relative_manifest:
            indexed_manifest_path = self.root / relative_manifest
            resolved_manifest = indexed_manifest_path.resolve(strict=False)
            if not any(
                resolved_manifest.is_relative_to(root.resolve(strict=False))
                for root in (self.root, self.items_root)
            ):
                indexed_manifest_path = Path()
            if indexed_manifest_path.is_file():
                manifest = self.read_manifest(indexed_manifest_path)
                if _manifest_matches_paper_address(manifest, target):
                    return indexed_manifest_path, manifest
        for indexed_manifest_path in self.manifest_paths():
            manifest = self.read_manifest(indexed_manifest_path)
            if _manifest_matches_paper_address(manifest, target):
                return indexed_manifest_path, manifest
        return None

    def paper_dir_from_manifest(self, manifest: dict, manifest_path: Path | None = None) -> Path:
        root = str(manifest.get("root") or "").strip()
        if root:
            return Path(root)
        if manifest_path:
            return manifest_path.parent
        return self.root

    def resolve_manifest_file(self, manifest: dict, key: str, manifest_path: Path | None = None) -> Path | None:
        files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
        rel = str(files.get(key) or "").strip()
        if not rel:
            return None
        return _paper_bundle_path(
            self.paper_dir_from_manifest(manifest, manifest_path),
            rel,
        )

    def manifest_to_paper(self, manifest: dict, manifest_path: Path | None = None) -> dict:
        paper_dir = self.paper_dir_from_manifest(manifest, manifest_path)
        files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
        source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
        relations = manifest.get("relations") if isinstance(manifest.get("relations"), dict) else {}
        pdf_file = self.resolve_manifest_file(manifest, "pdf", manifest_path)
        analysis_file = self.resolve_manifest_file(manifest, "analysis", manifest_path)
        projects = relations.get("projects") if isinstance(relations.get("projects"), list) else []
        topics = relations.get("topics") if isinstance(relations.get("topics"), list) else []
        project_ids = [
            str(item.get("project_id") or "").strip()
            for item in projects
            if isinstance(item, dict) and _is_project_relation_id(item.get("project_id"))
        ]
        session_project = ""
        for item in projects:
            if not isinstance(item, dict):
                continue
            candidate_project = str(item.get("project_id") or "").strip()
            if _is_project_relation_id(candidate_project):
                session_project = candidate_project
                break
        paper_id = str(manifest.get("paper_id") or "").strip()
        raw = {
            "id": _validated_paper_id(paper_id),
            "slug": str(manifest.get("paper_key") or "").strip() or _slugify(str(manifest.get("title") or "")),
            "title": str(manifest.get("title") or "").strip(),
            "authors": _paper_authors_string(manifest.get("authors")),
            "venue": str(manifest.get("venue") or "Unsorted").strip(),
            "year": str(manifest.get("year") or "").strip(),
            "session": session_project,
            "folder": str(paper_dir),
            "abstract": str(manifest.get("abstract") or "").strip(),
            "tags": manifest.get("tags") or topics,
            "project_ids": project_ids,
            "relations": relations,
            "pdf_path": _manifest_pdf_path(manifest, pdf_file),
            "analysis_path": str(analysis_file) if analysis_file and analysis_file.is_file() else "",
            "status": str(manifest.get("status") or "registered").strip(),
            "created_by": str(source.get("created_by") or "").strip(),
            "created_at": _coerce_timestamp(source.get("created_at")),
            "updated_at": _coerce_timestamp(source.get("updated_at") or source.get("created_at")),
        }
        paper = _clean_paper(raw)
        # _clean_paper stamps the time of a write; a read keeps the manifest's own time.
        paper["updated_at"] = raw["updated_at"]
        paper["_store_root"] = str(paper_dir)
        paper["_manifest_path"] = str(manifest_path or paper_dir / "manifest.json")
        paper["_files"] = files
        paper["_identifiers"] = manifest.get("identifiers") if isinstance(manifest.get("identifiers"), dict) else {}
        return paper

    def all_papers(self) -> list[dict]:
        papers = []
        for path in self.manifest_paths():
            manifest = self.read_manifest(path)
            if not manifest:
                continue
            try:
                papers.append(self.manifest_to_paper(manifest, path))
            except Exception:
                continue
        return papers

    def paper_by_id(self, paper_id: str) -> dict | None:
        found = self.manifest_for_paper_id(paper_id)
        if not found:
            return None
        manifest_path, manifest = found
        return self.manifest_to_paper(manifest, manifest_path)

    def paper_dir_for_new(self, paper: dict) -> Path:
        year = _safe_year(paper.get("year")) or "unknown"
        identifiers = paper.get("_identifiers") if isinstance(paper.get("_identifiers"), dict) else {}
        arxiv = str(identifiers.get("arxiv") or _arxiv_id_from_text("\n".join([str(paper.get("pdf_path") or ""), str(paper.get("title") or "")]))).strip()
        if arxiv:
            key = f"arxiv_{re.sub(r'[^0-9A-Za-z]+', '_', arxiv).strip('_')}"
        else:
            key = _slugify(str(paper.get("slug") or paper.get("title") or paper.get("id") or "paper"))
        title_slug = _slugify(str(paper.get("title") or "paper"))
        base = self.items_root / year / f"{key}__{title_slug[:80]}"
        path = base
        suffix = 2
        while path.exists() and not (path / "manifest.json").is_file():
            path = Path(f"{base}-{suffix}")
            suffix += 1
        return path

    def fingerprint_map(self) -> dict:
        path = self.index_path("fingerprints.json")
        data = _load_json(path, {})
        return data if isinstance(data, dict) else {}

    def fingerprints_for_payload(self, payload: dict) -> list[str]:
        identifiers = payload.get("identifiers") if isinstance(payload.get("identifiers"), dict) else {}
        text = "\n".join(str(payload.get(key) or "") for key in ("title", "pdf_path", "url", "source_url", "doi", "openalex"))
        arxiv = str(identifiers.get("arxiv") or payload.get("arxiv") or _arxiv_id_from_text(text)).strip()
        doi = str(identifiers.get("doi") or payload.get("doi") or "").strip().lower()
        openalex = str(identifiers.get("openalex") or payload.get("openalex") or "").strip().lower()
        title = _normalize_fingerprint_text(str(payload.get("title") or ""))
        result = []
        if arxiv:
            arxiv_key = arxiv.lower()
            result.append(f"arxiv:{arxiv_key}")
            arxiv_base = re.sub(r"v\d+$", "", arxiv_key)
            if arxiv_base and arxiv_base != arxiv_key:
                result.append(f"arxiv:{arxiv_base}")
        if doi:
            result.append(f"doi:{doi}")
        if openalex:
            result.append(f"openalex:{openalex}")
        if title:
            result.append(f"title:{title}")
        return result

    def find_existing_id(self, payload: dict) -> str:
        fp_map = self.fingerprint_map()
        for fp in self.fingerprints_for_payload(payload):
            paper_id = str(fp_map.get(fp) or "").strip()
            if paper_id:
                return paper_id
        return ""

    def manifest_from_paper(self, paper: dict, paper_dir: Path, existing: dict | None = None) -> dict:
        existing = existing if isinstance(existing, dict) else {}
        identifiers = dict(existing.get("identifiers") or {})
        identifiers.update(paper.get("_identifiers") if isinstance(paper.get("_identifiers"), dict) else {})
        arxiv = str(identifiers.get("arxiv") or _arxiv_id_from_text(str(paper.get("pdf_path") or ""))).strip()
        if arxiv:
            identifiers["arxiv"] = arxiv
        source = dict(existing.get("source") or {})
        source.update({
            "pdf_url": _paper_source_pdf_url(paper, source),
            "pdf_download_error": str(paper.get("_pdf_download_error") or source.get("pdf_download_error") or ""),
            "created_by": str(paper.get("created_by") or source.get("created_by") or ""),
            "created_at": _coerce_timestamp(source.get("created_at") or paper.get("created_at")),
            "updated_at": _now(),
        })
        files = dict(existing.get("files") or {})
        files.update({
            "summary": "summary.md",
            "analysis": "analysis.md" if (paper_dir / "analysis.md").is_file() else files.get("analysis", ""),
            "exp": "exp.md" if (paper_dir / "exp.md").is_file() else files.get("exp", ""),
            "pdf_text_raw": "text/pdf.raw.txt",
            "pdf_text_clean": "text/pdf.clean.txt",
            "reviews_dir": "reviews",
            "notes_dir": "notes",
            "refs": "refs/references.json",
            "figures": "figures/figures.json",
            "figures_dir": "figures",
        })
        if (paper_dir / "paper.pdf").is_file():
            files["pdf"] = "paper.pdf"
        else:
            files.pop("pdf", None)
        relations = _merge_paper_relations(
            existing.get("relations") if isinstance(existing.get("relations"), dict) else {},
            paper,
        )
        tags = _merge_project_tags(paper.get("tags") or [], paper)
        manifest = {
            "schema": "tfi.paper.v1",
            "paper_id": paper["id"],
            "paper_key": str(existing.get("paper_key") or paper.get("slug") or _slugify(paper["title"])),
            "root": str(paper_dir),
            "title": paper["title"],
            "authors": _paper_authors_list(paper.get("authors")),
            "year": str(paper.get("year") or ""),
            "venue": _normalize_venue(paper.get("venue")),
            "abstract": str(paper.get("abstract") or "").strip(),
            "identifiers": identifiers,
            "fingerprints": self.fingerprints_for_payload({**paper, "identifiers": identifiers}),
            "source": source,
            "files": files,
            "relations": relations,
            "tags": tags,
            "status": str(paper.get("status") or "registered"),
        }
        return manifest

    def write_manifest(self, paper: dict, paper_dir: Path, existing: dict | None = None) -> dict:
        self.ensure()
        paper_dir.mkdir(parents=True, exist_ok=True)
        for subdir in ("text", "reviews", "notes", "refs", "figures", "runs"):
            (paper_dir / subdir).mkdir(parents=True, exist_ok=True)
        manifest = self.manifest_from_paper(paper, paper_dir, existing=existing)
        _write_json(paper_dir / "manifest.json", manifest)
        return manifest

    def ingest_file(self, source: str, target: Path, *, retries: int = 3, retry_backoff_s: float = 2.0) -> bool:
        text = str(source or "").strip()
        if not text or target.is_file():
            return False
        if _is_url(text):
            attempts = max(1, int(retries))
            tmp = target.with_name(target.name + ".download")
            last_error: Exception | None = None
            for attempt in range(attempts):
                try:
                    req = urllib.request.Request(text, headers={"User-Agent": "TFIPortalPaperStore/0.1"})
                    with urllib.request.urlopen(req, timeout=270) as response:
                        data = response.read()
                    if data:
                        tmp.write_bytes(data)
                        tmp.replace(target)
                        return True
                    last_error = RuntimeError("empty response")
                except Exception as exc:
                    last_error = exc
                    try:
                        tmp.unlink(missing_ok=True)
                    except Exception:
                        pass
                if attempt + 1 < attempts:
                    time.sleep(max(0.0, float(retry_backoff_s)) * (attempt + 1))
            raise RuntimeError(f"download failed after {attempts} attempts: {last_error}") from last_error
        src = _safe_file_path(text)
        if src and src.is_file() and src.resolve(strict=False) != target.resolve(strict=False):
            shutil.copy2(src, target)
            return True
        return False

    def register(self, payload: dict, actor: str) -> dict:
        existing_id = self.find_existing_id(payload)
        if existing_id:
            found = self.manifest_for_paper_id(existing_id)
            if found:
                manifest_path, manifest = found
                paper = _clean_paper({**payload, "id": existing_id}, actor=actor)
                manifest["relations"] = _merge_paper_relations(
                    manifest.get("relations") if isinstance(manifest.get("relations"), dict) else {},
                    paper,
                )
                manifest["tags"] = _merge_project_tags(
                    manifest.get("tags") if isinstance(manifest.get("tags"), list) else [],
                    paper,
                )
                manifest["source"] = {
                    **(manifest.get("source") if isinstance(manifest.get("source"), dict) else {}),
                    "updated_at": _now(),
                }
                _write_json(manifest_path, manifest)
                self.rebuild_fingerprint_index()
                return self.manifest_to_paper(manifest, manifest_path)
        clean_payload = dict(payload)
        if not str(clean_payload.get("id") or "").strip():
            existing_ids = []
            for manifest_path in self.manifest_paths():
                manifest = self.read_manifest(manifest_path)
                paper_id = str((manifest or {}).get("paper_id") or "").strip()
                if paper_id:
                    existing_ids.append(paper_id)
            clean_payload["id"] = get_resource_service().new_id(
                "paper",
                existing_ids,
            )
        paper = _clean_paper(clean_payload, actor=actor)
        source_pdf_url = str(payload.get("source_pdf_url") or "").strip()
        if _is_url(source_pdf_url):
            paper["_source_pdf_url"] = source_pdf_url
        identifiers = payload.get("identifiers") if isinstance(payload.get("identifiers"), dict) else {}
        paper["_identifiers"] = {
            key: str(value).strip()
            for key, value in {
                **identifiers,
                "arxiv": identifiers.get("arxiv") or payload.get("arxiv") or _arxiv_id_from_text(str(payload.get("pdf_path") or "")),
                "doi": identifiers.get("doi") or payload.get("doi") or "",
                "openalex": identifiers.get("openalex") or payload.get("openalex") or "",
            }.items()
            if str(value or "").strip()
        }
        paper_dir = self.paper_dir_for_new(paper)
        paper_dir.mkdir(parents=True, exist_ok=True)
        pdf_source = str(payload.get("pdf_path") or "")
        pdf_error = ""
        try:
            self.ingest_file(pdf_source, paper_dir / "paper.pdf")
        except Exception as exc:
            pdf_error = str(exc)
            paper["_source_pdf_url"] = pdf_source if _is_url(pdf_source) else ""
            paper["_pdf_download_error"] = pdf_error
            paper["pdf_path"] = ""
            if str(paper.get("status") or "").strip() in {"", "auto-collected", "registered"}:
                paper["status"] = "pdf-missing"
            paper["abstract"] = _append_pdf_pending_note(paper.get("abstract"), pdf_source, pdf_error)
        self.ingest_file(str(payload.get("analysis_path") or ""), paper_dir / "analysis.md", retries=1)
        if (paper_dir / "paper.pdf").is_file():
            paper["pdf_path"] = str(paper_dir / "paper.pdf")
        if (paper_dir / "analysis.md").is_file():
            paper["analysis_path"] = str(paper_dir / "analysis.md")
        manifest = self.write_manifest(paper, paper_dir)
        self.rebuild_fingerprint_index()
        return self.manifest_to_paper(manifest, paper_dir / "manifest.json")

    def update_paper(self, paper_id: str, fields: dict) -> dict | None:
        found = self.manifest_for_paper_id(paper_id)
        if not found:
            return None
        path, manifest = found
        paper = self.manifest_to_paper(manifest, path)
        updated = _clean_paper({**paper, **fields, "updated_at": _now()})
        paper_dir = self.paper_dir_from_manifest(manifest, path)
        if str(fields.get("analysis_path") or ""):
            analysis = _safe_file_path(str(fields.get("analysis_path") or ""))
            if analysis and analysis.is_file() and analysis.resolve(strict=False) != (paper_dir / "analysis.md").resolve(strict=False):
                shutil.copy2(analysis, paper_dir / "analysis.md")
                updated["analysis_path"] = str(paper_dir / "analysis.md")
        manifest = self.write_manifest(updated, paper_dir, existing=manifest)
        self.rebuild_fingerprint_index()
        return self.manifest_to_paper(manifest, paper_dir / "manifest.json")

    def replace_project(self, project_id: str, new_project_id: str = "", *, write: bool = True) -> list[str]:
        """Point the papers related to ``project_id`` at ``new_project_id``, or unlink them when it is empty.

        Papers live in the shared library, outside every project folder, so a
        renamed or deleted Project would otherwise leave relations to an ID that
        no longer exists.  Returns the IDs of the papers that change.
        """
        old = str(project_id or "").strip()
        new = str(new_project_id or "").strip()
        changed: list[str] = []
        if not old or old == new:
            return changed
        for path in self.manifest_paths():
            manifest = self.read_manifest(path)
            relations = manifest.get("relations") if isinstance(manifest.get("relations"), dict) else {}
            projects = relations.get("projects") if isinstance(relations.get("projects"), list) else []
            linked = {str(item.get("project_id") or "").strip() for item in projects if isinstance(item, dict)}
            if old not in linked:
                continue
            kept = []
            for item in projects:
                if isinstance(item, dict) and str(item.get("project_id") or "").strip() == old:
                    if not new or new in linked:
                        continue
                    item = {**item, "project_id": new}
                kept.append(item)
            changed.append(str(manifest.get("paper_id") or path.parent.name))
            if write:
                manifest["relations"] = {**relations, "projects": kept}
                manifest["source"] = {
                    **(manifest.get("source") if isinstance(manifest.get("source"), dict) else {}),
                    "updated_at": _now(),
                }
                _write_json(path, manifest)
        return changed

    def delete_paper(self, paper_id: str) -> dict | None:
        found = self.manifest_for_paper_id(paper_id)
        if not found:
            return None
        manifest_path, manifest = found
        paper_dir = self.paper_dir_from_manifest(manifest, manifest_path)
        try:
            resolved_dir = paper_dir.resolve(strict=False)
            resolved_items = self.items_root.resolve(strict=False)
            resolved_root = self.root.resolve(strict=False)
        except Exception:
            return None
        if not (resolved_dir == resolved_items or resolved_items in resolved_dir.parents):
            raise ValueError(f"refusing to delete path outside Paper items: {paper_dir}")
        if resolved_dir == resolved_items or resolved_dir == resolved_root:
            raise ValueError(f"refusing to delete unsafe Paper path: {paper_dir}")
        self.ensure()
        trash_name = f"{time.strftime('%Y%m%d-%H%M%S')}__{str(paper_id or '').strip() or paper_dir.name}"
        trash_path = self.root / "_trash" / _slugify(trash_name)
        suffix = 2
        while trash_path.exists():
            trash_path = self.root / "_trash" / f"{_slugify(trash_name)}-{suffix}"
            suffix += 1
        if paper_dir.exists():
            shutil.move(str(paper_dir), str(trash_path))
        self.rebuild_fingerprint_index()
        return {
            "paper_id": str(manifest.get("paper_id") or paper_id),
            "title": str(manifest.get("title") or ""),
            "deleted_path": str(paper_dir),
            "trash_path": str(trash_path),
        }

    def rebuild_fingerprint_index(self) -> dict[str, str]:
        self.ensure()
        fingerprints: dict[str, str] = {}
        manifests: dict[str, str] = {}
        for path in self.manifest_paths():
            manifest = self.read_manifest(path)
            if not manifest:
                continue
            paper_id = str(manifest.get("paper_id") or "").strip()
            if not paper_id:
                continue
            manifests[paper_id] = path.relative_to(self.root).as_posix()
            for fp in manifest.get("fingerprints") or []:
                fingerprints[str(fp)] = paper_id
        _write_json(self.index_path("fingerprints.json"), fingerprints)
        _write_json(self.index_path("manifests.json"), manifests)
        return fingerprints

    def paper_file(self, paper: dict, key: str) -> Path | None:
        root = str(paper.get("_store_root") or "").strip()
        files = paper.get("_files") if isinstance(paper.get("_files"), dict) else {}
        rel = str(files.get(key) or "").strip()
        if root and key == "refs" and not rel:
            rel = "refs/references.json"
        if root and key == "figures" and not rel:
            rel = "figures/figures.json"
        if root and key == "figures_dir" and not rel:
            rel = "figures"
        if root and key == "summary" and not rel:
            rel = "summary.md"
        if root and key == "exp" and not rel:
            rel = "exp.md"
        if not root or not rel:
            return None
        return _paper_bundle_path(Path(root), rel)

    def write_run_record(self, job: dict) -> None:
        paper_id = str(job.get("paper_id") or "").strip()
        job_id = str(job.get("job_id") or "").strip()
        if not paper_id or not job_id:
            return
        found = self.manifest_for_paper_id(paper_id)
        if not found:
            return
        paper_dir = self.paper_dir_from_manifest(found[1], found[0])
        runs_dir = paper_dir / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        _write_json(runs_dir / f"{_slugify(job_id)}.json", job)


PAPER_STORE = PaperStore()


def project_relations(project_id: str, new_project_id: str = "", *, write: bool = True) -> list[str]:
    """The ``project.relations`` hook (``portal_features``): papers follow a renamed Project or leave a deleted one."""
    return PAPER_STORE.replace_project(project_id, new_project_id, write=write)


def _paper_run_records(paper_id: str, *, kind: str = "", limit: int = 20) -> list[dict]:
    paper_id = str(paper_id or "").strip()
    if not paper_id:
        return []
    found = PAPER_STORE.manifest_for_paper_id(paper_id)
    if not found:
        return []
    manifest_path, manifest = found
    runs_dir = PAPER_STORE.paper_dir_from_manifest(manifest, manifest_path) / "runs"
    if not runs_dir.is_dir():
        return []
    records = []
    for path in sorted(runs_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        item = _load_json(path, {})
        if not isinstance(item, dict):
            continue
        if kind and str(item.get("kind") or "") != kind:
            continue
        records.append(item)
        if limit and len(records) >= limit:
            break
    records.sort(key=lambda item: int(item.get("updated_at") or item.get("finished_at") or item.get("started_at") or 0), reverse=True)
    return records


def _latest_paper_run(paper_id: str, *, kind: str) -> dict:
    records = _paper_run_records(paper_id, kind=kind, limit=1)
    return records[0] if records else {}


def _write_paper_run_record(paper_id: str, job: dict) -> None:
    paper_id = str(paper_id or "").strip()
    if not paper_id or not isinstance(job, dict):
        return
    payload = {
        "paper_id": paper_id,
        "updated_at": _now(),
        **job,
    }
    PAPER_STORE.write_run_record(payload)
    _notify_paper_projection(
        paper_id,
        fields=[f"run:{str(payload.get('kind') or 'paper').strip()}"],
    )


def _paper_run_record_by_job_id(paper_id: str, job_id: str) -> dict:
    paper_id = str(paper_id or "").strip()
    job_id = str(job_id or "").strip()
    if not paper_id or not job_id:
        return {}
    found = PAPER_STORE.manifest_for_paper_id(paper_id)
    if not found:
        return {}
    manifest_path, manifest = found
    runs_dir = PAPER_STORE.paper_dir_from_manifest(manifest, manifest_path) / "runs"
    path = runs_dir / f"{_slugify(job_id)}.json"
    item = _load_json(path, {})
    return item if isinstance(item, dict) else {}


def _update_paper_run_record(paper_id: str, job: dict) -> None:
    if not isinstance(job, dict):
        return
    job_id = str(job.get("job_id") or "").strip()
    if not job_id:
        _write_paper_run_record(paper_id, job)
        return
    PAPER_RUN_LOCK_ROOT.mkdir(parents=True, exist_ok=True)
    lock_key = hashlib.sha256(
        f"{paper_id}\n{job_id}".encode("utf-8")
    ).hexdigest()
    with exclusive_file_lock(
        PAPER_RUN_LOCK_ROOT / f"{lock_key}.lock"
    ):
        existing = _paper_run_record_by_job_id(
            paper_id,
            job_id,
        )
        merged = {**existing, **job}
        _write_paper_run_record(paper_id, merged)


def _ensure_data_root() -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)


def _load_json(path: Path, default):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, type(default)) else default
    except Exception:
        return default


def _write_json(path: Path, payload) -> None:
    _ensure_data_root()
    atomic_replace_json(path, payload, indent=2, sort_keys=False)


def _normalize_list(value) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = re.split(r"[,，\n]+", str(value or ""))
    result = []
    seen = set()
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text[:80])
    return result


def _project_ids_from_paper(paper: dict) -> list[str]:
    values: list[str] = []
    values.extend(str(item) for item in paper.get("project_ids") or [] if _is_project_relation_id(item))
    relations = paper.get("relations") if isinstance(paper.get("relations"), dict) else {}
    for item in relations.get("projects") or []:
        if isinstance(item, dict):
            project_id = str(item.get("project_id") or "").strip()
        else:
            project_id = str(item or "").strip()
        if _is_project_relation_id(project_id):
            values.append(project_id)
    session = str(paper.get("session") or "").strip()
    if _is_project_relation_id(session):
        values.append(session)
    return _normalize_list(values)


def _merge_project_tags(tags: list[str], paper: dict) -> list[str]:
    project_ids_list = _project_ids_from_paper(paper)
    project_ids = set(project_ids_list)
    values = [
        str(tag)
        for tag in tags or []
        if not _is_non_project_tag(tag) and not _is_project_tag(tag, project_ids)
    ]
    return _normalize_list(values)


def _paper_project_filter_values(paper: dict) -> list[str]:
    values: list[str] = []
    values.extend(str(item) for item in paper.get("project_ids") or [] if _is_project_relation_id(item))
    relations = paper.get("relations") if isinstance(paper.get("relations"), dict) else {}
    for item in relations.get("projects") or []:
        if isinstance(item, dict):
            if _is_project_relation_id(item.get("project_id")):
                values.append(str(item.get("project_id") or ""))
        else:
            if _is_project_relation_id(item):
                values.append(str(item or ""))
    for tag in paper.get("tags") or []:
        text = str(tag or "").strip()
        if text.casefold().startswith("project:") and not _is_non_project_tag(text):
            values.append(text.split(":", 1)[1].strip())
    return _normalize_list(values)


def _registered_project_ids() -> set[str]:
    from management_portal import project_registry

    return {str(item.get("id") or "").strip() for item in project_registry.projects()}


def _merge_paper_relations(existing: dict, paper: dict) -> dict:
    """Merge project relations, keeping only Projects that are registered.

    Collection jobs passed journals and topics such as "Science Robotics" as the
    paper's project, which left relations to Projects that never existed.
    """
    relations = dict(existing or {})
    existing_projects = relations.get("projects") if isinstance(relations.get("projects"), list) else []
    by_project: dict[str, dict] = {}
    for item in existing_projects:
        if not isinstance(item, dict):
            continue
        project_id = str(item.get("project_id") or "").strip()
        if _is_project_relation_id(project_id):
            by_project[project_id] = dict(item)
    incoming = paper.get("relations") if isinstance(paper.get("relations"), dict) else {}
    incoming_projects = incoming.get("projects") if isinstance(incoming.get("projects"), list) else []
    for item in incoming_projects:
        if not isinstance(item, dict):
            continue
        project_id = str(item.get("project_id") or "").strip()
        if _is_project_relation_id(project_id):
            by_project[project_id] = {**by_project.get(project_id, {}), **dict(item), "project_id": project_id}
    for project_id in _project_ids_from_paper(paper):
        by_project.setdefault(
            project_id,
            {
                "project_id": project_id,
                "relation": "relevant",
                "source": str(paper.get("created_by") or ""),
            },
        )
    project_ids = set(by_project)
    registered = _registered_project_ids()
    relations["projects"] = sorted(
        (item for project_id, item in by_project.items() if project_id in registered),
        key=lambda item: str(item.get("project_id") or ""),
    )
    topics = relations.get("topics") if isinstance(relations.get("topics"), list) else []
    relations["topics"] = _normalize_list(
        [
            tag for tag in [*topics, *(paper.get("tags") or [])]
            if not _is_non_project_tag(tag) and not _is_project_tag(tag, project_ids)
        ]
    )
    return relations


def _slugify(text: str) -> str:
    raw = str(text or "").strip().lower()
    raw = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", raw)
    raw = re.sub(r"-+", "-", raw).strip("-")
    return raw[:80] or secrets.token_hex(4)


def _is_url(value: str) -> bool:
    parsed = urlparse(str(value or ""))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _safe_file_path(raw: str) -> Path | None:
    text = str(raw or "").strip()
    if not text or _is_url(text):
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = DATA_ROOT / path
    resolved = path.resolve(strict=False)
    for root in (*ALLOWED_FILE_ROOTS, PAPER_STORE.items_root):
        root_resolved = root.resolve(strict=False)
        try:
            resolved.relative_to(root_resolved)
            return resolved
        except ValueError:
            continue
    return None


def _paper_store_file(paper: dict, key: str) -> Path | None:
    return PAPER_STORE.paper_file(paper, key)


def _read_text_preview(path: Path | None, limit: int = 120_000) -> str:
    if not path or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:
        return ""


def _safe_year(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"(19|20)\d{2}", text)
    return match.group(0) if match else text[:16]


def _normalize_venue(value) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return "Unsorted"
    if text.casefold().startswith("arxiv"):
        return "arXiv"
    return text[:80] or "Unsorted"


def _clean_paper(raw: dict, actor: str = "") -> dict:
    now = _now()
    paper = {key: raw.get(key) for key in PAPER_FIELDS if key in raw}
    title = str(paper.get("title") or "").strip()
    if not title:
        raise ValueError("title required")
    paper["id"] = str(
        paper.get("id") or get_resource_service().new_id("paper")
    ).strip()
    paper["id"] = _validated_paper_id(paper["id"])
    paper["slug"] = str(paper.get("slug") or _slugify(f"{paper.get('venue') or ''}-{paper.get('year') or ''}-{title}")).strip()
    paper["title"] = title[:320]
    paper["authors"] = str(paper.get("authors") or "").strip()[:600]
    paper["venue"] = _normalize_venue(paper.get("venue"))
    paper["year"] = _safe_year(paper.get("year"))
    session = str(paper.get("session") or "").strip()[:120]
    paper["session"] = session if _is_project_relation_id(session) else ""
    paper["folder"] = str(paper.get("folder") or "").strip()[:240]
    paper["abstract"] = str(paper.get("abstract") or "").strip()[:5000]
    project_ids_list = _project_ids_from_paper(paper)
    project_ids = set(project_ids_list)
    paper["tags"] = _normalize_list(
        [
            tag for tag in _normalize_list(paper.get("tags"))
            if not _is_non_project_tag(tag) and not _is_project_tag(tag, project_ids)
        ]
    )
    paper["project_ids"] = _normalize_list(project_ids_list)
    relations = dict(paper.get("relations") if isinstance(paper.get("relations"), dict) else {})
    projects = relations.get("projects") if isinstance(relations.get("projects"), list) else []
    relations["projects"] = [
        item for item in projects
        if isinstance(item, dict) and _is_project_relation_id(item.get("project_id"))
    ]
    paper["relations"] = relations
    paper["workload"] = _normalize_list(paper.get("workload"))
    paper["platform"] = _normalize_list(paper.get("platform"))
    paper["architecture"] = _normalize_list(paper.get("architecture"))
    paper["optimization"] = _normalize_list(paper.get("optimization"))
    paper["pdf_path"] = str(paper.get("pdf_path") or "").strip()
    paper["analysis_path"] = str(paper.get("analysis_path") or "").strip()
    paper["status"] = str(paper.get("status") or "registered").strip()[:40] or "registered"
    paper["created_by"] = str(paper.get("created_by") or actor or "").strip()
    paper["created_at"] = int(paper.get("created_at") or now)
    paper["updated_at"] = now
    return paper


def _paper_search_text(paper: dict) -> str:
    parts = []
    for key in ("id", "paper_id", "title", "authors", "venue", "year", "session", "folder", "abstract", "status"):
        parts.append(str(paper.get(key) or ""))
    for key in ("tags", "workload", "platform", "architecture", "optimization"):
        parts.extend(str(x) for x in paper.get(key) or [])
    if paper.get("_store_root") or paper.get("analysis_path"):
        analysis_path = _safe_file_path(str(paper.get("analysis_path") or ""))
        parts.append(_read_text_preview(analysis_path, limit=40_000))
        parts.append(_summary_text(paper, limit=4000))
    return "\n".join(parts).casefold()


def _all_papers() -> list[dict]:
    return PAPER_STORE.all_papers()


def _all_reviews() -> list[dict]:
    return paper_state_store.records("reviews")


def _all_review_jobs() -> list[dict]:
    return paper_state_store.records("review_jobs")


def _announce_review_changes() -> None:
    """Ask the review index for a rescan after the review list changed.

    The index joins the stored reviews with each package's
    reviews/review_*.md; the review table has no file a watcher could see.
    """
    try:
        get_resource_service().notify_source_changed(resource_type="review", kind="updated")
    except Exception:
        logging.getLogger(__name__).warning("Paper review index notification failed", exc_info=True)


def _refs_for_paper(paper: dict) -> list[dict]:
    refs_path = _paper_store_file(paper, "refs")
    refs = _load_json(refs_path, []) if refs_path else []
    return [item for item in refs if isinstance(item, dict)] if isinstance(refs, list) else []


def _ref_context(ref_id: str, paper_id: str = "") -> tuple[dict, dict, list[dict]]:
    target_ref_id = str(ref_id or "").strip()
    target_paper_id = str(paper_id or "").strip()
    if not target_ref_id:
        raise ValueError("ref_id required")
    if not target_paper_id:
        raise ValueError("paper_id required")
    paper = _paper_by_id(target_paper_id)
    if not paper:
        raise KeyError("paper not found")
    refs = _refs_for_paper(paper)
    ref = next((item for item in refs if str(item.get("ref_id") or "") == target_ref_id), None)
    if not ref:
        raise KeyError("ref not found")
    return paper, ref, refs


def _paper_job_store_lock(path: Path):
    return exclusive_file_lock(
        path.with_suffix(path.suffix + ".lock")
    )


def _set_review_job(job_id: str, patch: dict) -> dict:
    with LOCK:
        found = paper_state_store.merge("review_jobs", job_id, {**patch, "updated_at": _now()})
    _notify_paper_projection(
        found.get("paper_id") or job_id,
        actors=[found.get("created_by")],
        fields=["review"],
    )
    return found


def _review_job_by_id(job_id: str) -> dict | None:
    return paper_state_store.get("review_jobs", str(job_id or "").strip())


def _all_paper_collection_jobs() -> list[dict]:
    return paper_state_store.records("collector_jobs")


def _set_paper_collection_job(job_id: str, patch: dict) -> dict:
    with LOCK:
        found = paper_state_store.merge("collector_jobs", job_id, {**patch, "updated_at": _now()})
    _notify_paper_projection(job_id, fields=["collector"])
    return found


def _paper_collection_job_by_id(job_id: str) -> dict | None:
    return paper_state_store.get("collector_jobs", str(job_id or "").strip())


def _set_job_with_run_record(kind: str, job_id: str, patch: dict) -> dict:
    # The run record is written before the job's lock is released, so run
    # records of one job are written in the same order as the job itself.
    with LOCK, paper_state_store.locked(kind):
        found = paper_state_store.merge(kind, job_id, {**patch, "updated_at": _now()})
        try:
            PAPER_STORE.write_run_record(found)
        except Exception:
            pass
    return found


def _set_deep_read_job(job_id: str, patch: dict) -> dict:
    found = _set_job_with_run_record("deep_read_jobs", job_id, patch)
    _notify_paper_projection(
        found.get("paper_id") or job_id,
        fields=["deep_read"],
    )
    return found


def _set_exp_job(job_id: str, patch: dict) -> dict:
    found = _set_job_with_run_record("exp_jobs", job_id, patch)
    _notify_paper_projection(
        found.get("paper_id") or job_id,
        fields=["exp"],
    )
    return found


def _latest_job(kind: str, *, paper_id: str = "", job_id: str = "") -> dict | None:
    if job_id:
        found = paper_state_store.get(kind, job_id)
        matches = [found] if found else []
    else:
        matches = paper_state_store.records(kind, paper_id=paper_id)
    if not matches:
        return None
    return sorted(matches, key=lambda item: int(item.get("created_at") or item.get("updated_at") or 0), reverse=True)[0]


def _latest_deep_read_job(*, paper_id: str = "", job_id: str = "") -> dict | None:
    return _latest_job("deep_read_jobs", paper_id=paper_id, job_id=job_id)


def _latest_exp_job(*, paper_id: str = "", job_id: str = "") -> dict | None:
    return _latest_job("exp_jobs", paper_id=paper_id, job_id=job_id)


def _is_stale_exp_job(job: dict) -> bool:
    status = str(job.get("status") or "")
    if status not in {"queued", "running"}:
        return False
    touched = int(job.get("updated_at") or job.get("created_at") or 0)
    return touched > 0 and _now() - touched > EXP_STALE_SECONDS


def _mark_stale_exp_job(job: dict) -> dict:
    return _set_exp_job(
        str(job.get("job_id") or ""),
        {
            "status": "error",
            "step": "stale",
            "progress": 100,
            "error": "Exp task was interrupted or stopped updating. Please generate Exp again.",
        },
    )


def _update_paper_fields(paper_id: str, fields: dict) -> dict:
    with LOCK:
        updated = PAPER_STORE.update_paper(paper_id, fields)
        if not updated:
            raise KeyError("paper not found")
    _notify_paper_projection(
        paper_id,
        fields=list(fields),
    )
    return updated


def _paper_public(paper: dict) -> dict:
    categories = []
    for key in ("workload", "platform", "architecture", "optimization"):
        categories.extend(paper.get(key) or [])
    if {"has_pdf", "has_analysis", "has_summary"}.issubset(paper):
        return {
            **paper,
            "has_pdf": bool(paper.get("has_pdf")),
            "has_analysis": bool(paper.get("has_analysis")),
            "has_summary": bool(paper.get("has_summary")),
            "category_tags": categories,
            "tag_count": len(paper.get("tags") or []) + len(categories),
        }
    pdf_path = str(paper.get("pdf_path") or "").strip()
    analysis_path = str(paper.get("analysis_path") or "").strip()
    pdf_file = _safe_file_path(pdf_path)
    analysis_file = _safe_file_path(analysis_path)
    summary_file = _paper_store_file(paper, "summary")
    return {
        **paper,
        "has_pdf": bool(pdf_file and pdf_file.is_file()),
        "has_analysis": bool(_is_url(analysis_path) or (analysis_file and analysis_file.is_file())),
        "has_summary": bool(summary_file and summary_file.is_file() and _read_text_preview(summary_file, limit=20).strip()),
        "category_tags": categories,
        "tag_count": len(paper.get("tags") or []) + len(categories),
    }


def _paper_library_list_item(paper: dict) -> dict:
    keys = (
        "id", "title", "authors", "venue", "year", "session",
        "tags", "workload", "platform", "architecture", "optimization",
        "created_at", "updated_at", "has_pdf", "has_analysis", "has_summary",
        "semantic_rank", "semantic_score", "semantic_reason", "collection_rank",
    )
    item = {key: paper.get(key) for key in keys if paper.get(key) not in (None, "", [])}
    abstract = str(paper.get("abstract") or "").strip()
    if abstract:
        item["abstract"] = abstract[:300]
    return item


def _paper_library_tree_item(paper: dict) -> dict:
    """Fields needed to browse and export papers before their row is shown."""
    return {
        key: paper.get(key)
        for key in ("id", "title", "venue", "year", "session", "has_pdf")
        if paper.get(key) not in (None, "")
    }


def _paper_library_page_number(value: object, default: int, maximum: int) -> int:
    try:
        return max(0, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def _paper_store_for_root(paper_root: str | Path | None = None) -> PaperStore:
    if paper_root is None:
        return PAPER_STORE
    root = Path(paper_root).expanduser().resolve(strict=False)
    if root == PAPER_STORE.root.expanduser().resolve(strict=False):
        return PAPER_STORE
    return PaperStore(root)


def scan_and_clean_paper_ids(
    *,
    paper_root: str | Path | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    store = _paper_store_for_root(paper_root)
    changes: list[tuple[Path, dict]] = []
    invalid: list[dict[str, str]] = []
    collisions: list[dict[str, str]] = []
    seen: dict[str, str] = {}
    unchanged_count = 0
    paper_count = 0

    for path in store.manifest_paths():
        manifest = store.read_manifest(path)
        paper_id = str(manifest.get("paper_id") or "").strip()
        if not paper_id:
            invalid.append({"path": str(path), "error": "paper_id required"})
            continue
        paper_count += 1
        try:
            paper_id = _validated_paper_id(paper_id)
        except ValueError as exc:
            invalid.append({"path": str(path), "paper_id": paper_id, "error": str(exc)})
            continue
        previous = seen.get(paper_id)
        if previous:
            collisions.append(
                {"paper_id": paper_id, "path": str(path), "conflicts_with": previous}
            )
            continue
        seen[paper_id] = str(path)
        unchanged_count += 1

    if apply and (invalid or collisions):
        raise ValueError(
            f"Paper identity update blocked: {len(invalid)} invalid manifest(s), "
            f"{len(collisions)} collision(s)"
        )
    return {
        "schema": "tfi.paper_identity_scan.v1",
        "apply": bool(apply),
        "paper_root": str(store.root),
        "paper_count": paper_count,
        "changed_count": len(changes),
        "unchanged_count": unchanged_count,
        "invalid_count": len(invalid),
        "collision_count": len(collisions),
        "invalid": invalid,
        "collisions": collisions,
    }


def _paper_resource_record(paper: dict) -> dict[str, Any]:
    paper_id = _validated_paper_id(paper.get("id"))
    return {
        "id": paper_id,
        "type": "paper",
        "paper_id": paper_id,
        "title": str(paper.get("title") or "").strip(),
        "year": str(paper.get("year") or "").strip(),
        "venue": str(paper.get("venue") or "").strip(),
        "status": str(paper.get("status") or "").strip(),
        "root": str(paper.get("_store_root") or paper.get("folder") or "").strip(),
    }


def load_paper_resources(paper_root: str | Path | None = None) -> list[dict[str, Any]]:
    store = _paper_store_for_root(paper_root)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for paper in store.all_papers():
        record = _paper_resource_record(paper)
        if record["id"] in seen:
            raise ValueError(f"duplicate Paper resource ID: {record['id']}")
        seen.add(record["id"])
        records.append(record)
    return records


def _safe_paper_relative_path(raw_path: str) -> str:
    decoded = unquote(str(raw_path or ""))
    if not decoded or "\x00" in decoded or "\n" in decoded or "\r" in decoded:
        raise ValueError("invalid paper-relative path")
    return "/".join(relative_parts(PurePosixPath(decoded).as_posix()))


def resolve_paper_address(
    address: str,
    *,
    paper_root: str | Path | None = None,
) -> dict[str, Any]:
    clean_address = unquote(str(address or "").strip()).strip("/")
    root_id, separator, suffix = clean_address.partition("/")
    if not get_resource_service().is_valid_id(root_id, "paper"):
        raise ValueError(f"invalid Paper address: {address}")
    store = _paper_store_for_root(paper_root)
    paper = store.paper_by_id(root_id)
    if not paper:
        raise KeyError(root_id)
    paper_id = str(paper.get("id") or "").strip()
    paper_root_path = Path(
        str(paper.get("_store_root") or paper.get("folder") or store.root)
    ).resolve(strict=False)
    record = _paper_resource_record(paper)
    result: dict[str, Any] = {
        "schema": "tfi.resource_resolution.v2",
        "address": clean_address,
        "root_id": root_id,
        "resource_type": "paper",
        "target_kind": "paper",
        "record": record,
        "paper_id": paper_id,
        "paper_root": str(paper_root_path),
        "portal_url": portal_url_for_address(paper_id, "paper"),
        "exists": paper_root_path.is_dir(),
    }
    if not separator:
        return result
    parts = suffix.split("/")
    if len(parts) >= 2 and parts[0] == "file":
        relative_path = _safe_paper_relative_path("/".join(parts[1:]))
        target = (paper_root_path / relative_path).resolve(strict=False)
        try:
            target.relative_to(paper_root_path)
        except ValueError as exc:
            raise ValueError("paper-relative path escapes the Paper package") from exc
        result.update(
            {
                "target_kind": "file",
                "relative_path": relative_path,
                "resolved_path": str(target),
                "exists": target.is_file(),
            }
        )
        return result
    raise ValueError(f"unsupported nested Paper address: {address}")


def _filter_papers(papers: list[dict], params: dict) -> list[dict]:
    query = str(params.get("q") or "").strip().casefold()
    year = str(params.get("year") or "").strip()
    venue = str(params.get("venue") or "").strip().casefold()
    tag = str(params.get("tag") or "").strip().casefold()
    project = str(params.get("project") or "").strip().casefold()
    analysis = str(params.get("analysis") or "").strip().lower()
    registered = str(params.get("registered") or "").strip().lower()
    today = datetime.now().date()
    result = []
    for paper in papers:
        public = paper if {"has_pdf", "has_analysis", "has_summary"}.issubset(paper.keys()) else _paper_public(paper)
        if query and query not in _paper_search_text(paper):
            continue
        if year and str(paper.get("year") or "") != year:
            continue
        if venue and str(paper.get("venue") or "").casefold() != venue:
            continue
        if analysis == "ready" and not public["has_analysis"]:
            continue
        if analysis == "missing" and public["has_analysis"]:
            continue
        if registered == "today":
            created_at = _coerce_timestamp(paper.get("created_at"), default=0)
            if not created_at or datetime.fromtimestamp(created_at).date() != today:
                continue
        if tag:
            all_tags = []
            for key in ("tags", "workload", "platform", "architecture", "optimization"):
                all_tags.extend(str(x).casefold() for x in paper.get(key) or [])
            if tag not in all_tags:
                continue
        if project:
            project_values = []
            for value in _paper_project_filter_values(paper):
                text = str(value or "").strip().casefold()
                if text:
                    project_values.append(text)
                    project_values.append(f"project:{text}")
            if project not in project_values:
                continue
        result.append(public)
    return result


def _paper_query_sort_key(paper: dict, query: object) -> tuple[int, int, str, str]:
    needle = str(query or "").strip().casefold()
    paper_id = str(paper.get("id") or "").strip().casefold()
    title = str(paper.get("title") or "").strip().casefold()
    if needle and (paper_id == needle or title == needle):
        relevance = 0
    elif needle and (paper_id.startswith(needle) or title.startswith(needle)):
        relevance = 1
    elif needle and (needle in paper_id or needle in title):
        relevance = 2
    else:
        tags = []
        for key in ("tags", "workload", "platform", "architecture", "optimization"):
            tags.extend(str(value or "").strip().casefold() for value in paper.get(key) or [])
        if needle and any(value == needle for value in tags):
            relevance = 3
        elif needle and any(needle in value for value in tags):
            relevance = 4
        else:
            metadata = "\n".join(
                str(paper.get(key) or "")
                for key in ("authors", "venue", "year", "session", "folder", "abstract", "status")
            ).casefold()
            relevance = 5 if needle and needle in metadata else 6
    try:
        year = -int(str(paper.get("year") or "0").strip())
    except ValueError:
        year = 0
    return relevance, year, str(paper.get("venue") or "").casefold(), title


def _paper_collection_result_items(job: dict | None) -> list[dict]:
    if not job or job.get("deleted"):
        return []
    raw_results = job.get("results") if isinstance(job.get("results"), list) else []
    if not raw_results:
        result_path = str(job.get("result_path") or job.get("stdout_path") or "").strip()
        path = Path(result_path) if result_path else None
        parsed = {}
        if path and path.is_file():
            parsed = _load_json(path, {})
            if not isinstance(parsed, dict):
                try:
                    parsed = _extract_json_object(path.read_text(encoding="utf-8", errors="replace"))
                except Exception:
                    parsed = {}
        raw_results = parsed.get("registered") if isinstance(parsed.get("registered"), list) else []
    results = []
    seen = set()
    for idx, item in enumerate(raw_results):
        if not isinstance(item, dict):
            continue
        paper_id = str(item.get("paper_id") or "").strip()
        if not paper_id or paper_id in seen:
            continue
        seen.add(paper_id)
        results.append({
            "paper_id": paper_id,
            "rank": idx + 1,
            "title": str(item.get("title") or "").strip(),
        })
    return results


def _paper_collection_result_map(job_id: str) -> dict[str, dict]:
    job = _paper_collection_job_by_id(job_id)
    result_map = {}
    for item in _paper_collection_result_items(job):
        paper_id = str(item.get("paper_id") or "").strip()
        if paper_id and paper_id not in result_map:
            result_map[paper_id] = item
    return result_map


def _paper_collection_filter_summary(job_id: str) -> dict | None:
    job = _paper_collection_job_by_id(job_id)
    if not job or job.get("deleted"):
        return None
    results = _paper_collection_result_items(job)
    return {
        "job_id": str(job.get("job_id") or ""),
        "direction": str(job.get("direction") or ""),
        "status": str(job.get("status") or ""),
        "result_count": len(results),
        "registered_count": int(job["registered_count"] if job.get("registered_count") is not None else len(results)),
        "summary": str(job.get("summary") or ""),
        "error": str(job.get("error") or ""),
        "updated_at": int(job.get("updated_at") or 0),
    }


def _folder_tree(papers: list[dict], *, include_papers: bool = True) -> list[dict]:
    by_venue: dict[str, dict] = {}
    for paper in papers:
        venue = str(paper.get("venue") or "Unsorted")
        year = str(paper.get("year") or "Unknown")
        session = str(paper.get("session") or "General")
        venue_node = by_venue.setdefault(venue, {"name": venue, "count": 0, "children": {}})
        venue_node["count"] += 1
        year_node = venue_node["children"].setdefault(year, {"name": year, "count": 0, "children": {}})
        year_node["count"] += 1
        session_node = year_node["children"].setdefault(session, {"name": session, "count": 0, "papers": []})
        session_node["count"] += 1
        if include_papers:
            session_node["papers"].append({
                "id": paper["id"],
                "title": paper["title"],
                "has_analysis": paper.get("has_analysis", False),
                "has_pdf": paper.get("has_pdf", False),
            })
    venues = []
    for venue in sorted(by_venue.values(), key=lambda item: (-item["count"], item["name"])):
        years = []
        for year in sorted(venue["children"].values(), key=lambda item: str(item["name"]), reverse=True):
            sessions = sorted(year["children"].values(), key=lambda item: (-item["count"], item["name"]))
            year["children"] = sessions
            years.append(year)
        venue["children"] = years
        venues.append(venue)
    return venues


def _filters(papers: list[dict]) -> dict:
    values = {"years": set(), "venues": set(), "tags": set(), "projects": set(), "workload": set(), "platform": set(), "architecture": set(), "optimization": set()}
    for paper in papers:
        if paper.get("year"):
            values["years"].add(str(paper["year"]))
        if paper.get("venue"):
            values["venues"].add(str(paper["venue"]))
        values["projects"].update(str(x) for x in _paper_project_filter_values(paper) if str(x).strip())
        for key in ("tags", "workload", "platform", "architecture", "optimization"):
            values[key].update(str(x) for x in paper.get(key) or [] if str(x).strip())
    return {
        "years": sorted(values["years"], reverse=True),
        "venues": sorted(values["venues"]),
        "tags": sorted(values["tags"]),
        "projects": sorted(values["projects"]),
        "workload": sorted(values["workload"]),
        "platform": sorted(values["platform"]),
        "architecture": sorted(values["architecture"]),
        "optimization": sorted(values["optimization"]),
    }


def resource_index_papers(*, actor: str = "") -> list[dict]:
    from management_portal import resource_views

    return resource_views.paper_records(
        actor=actor,
        service=get_resource_service(),
    )


def semantic_search_catalog(*, actor: str, project: str = "") -> list[dict]:
    papers = resource_index_papers(actor=actor)
    return _filter_papers(papers, {"project": project}) if project else papers


def list_library(params: dict | None = None, *, actor: str = "") -> dict:
    params = params or {}
    compact = str(params.get("compact") or "").strip().lower() in {"1", "true", "yes", "on"}
    rows_only = compact and str(params.get("rows_only") or "").strip() == "1"
    semantic_id = str(params.get("semantic") or "").strip()
    collector_id = str(params.get("collector") or "").strip()
    semantic_view = semantic_search_jobs.search_view(semantic_id, actor=actor, surfaces=("paper",)) if semantic_id else None
    papers = resource_index_papers(actor=actor)
    favorites = paper_favorites.favorite_ids(actor)
    with LOCK:
        visible = _filter_papers(papers, params or {})
        reviews = [] if rows_only else _all_reviews()
    if str(params.get("favorite") or "").strip().lower() in {"1", "true", "yes", "on"}:
        selected = set(favorites)
        visible = [paper for paper in visible if paper.get("id") in selected]
    semantic_summary = semantic_view["summary"] if semantic_view else None
    semantic_results = semantic_view["result_map"] if semantic_view else {}
    semantic_rank = {paper_id: int(item.get("rank") or 0) - 1 for paper_id, item in semantic_results.items()}
    collection_summary = None
    collection_results = {}
    collection_rank = {}
    if semantic_id and semantic_summary:
        visible = [
            {
                **paper,
                "semantic_rank": semantic_results[str(paper.get("id") or "")]["rank"],
                "semantic_score": semantic_results[str(paper.get("id") or "")]["score"],
                "semantic_reason": semantic_results[str(paper.get("id") or "")]["reason"],
            }
            for paper in visible
            if str(paper.get("id") or "") in semantic_results
        ]
    elif collector_id:
        collection_summary = _paper_collection_filter_summary(collector_id)
        collection_results = _paper_collection_result_map(collector_id) if collection_summary else {}
        collection_rank = {paper_id: int(item.get("rank") or 0) - 1 for paper_id, item in collection_results.items()}
        if collection_summary:
            visible = [
                {
                    **paper,
                    "collection_rank": collection_results[str(paper.get("id") or "")]["rank"],
                }
                for paper in visible
                if str(paper.get("id") or "") in collection_results
            ]
    analysis_count = sum(1 for paper in papers if paper.get("has_analysis"))
    review_count = len(reviews)
    if semantic_rank:
        sorted_visible = sorted(
            visible,
            key=lambda p: (semantic_rank.get(str(p.get("id") or ""), 10_000), str(p.get("title") or "")),
        )
    elif collection_rank:
        sorted_visible = sorted(
            visible,
            key=lambda p: (collection_rank.get(str(p.get("id") or ""), 10_000), str(p.get("title") or "")),
        )
    elif str(params.get("q") or "").strip():
        sorted_visible = sorted(
            visible,
            key=lambda paper: _paper_query_sort_key(paper, params.get("q")),
        )
    else:
        sorted_visible = sorted(visible, key=lambda p: (str(p.get("year") or ""), str(p.get("venue") or ""), str(p.get("title") or "")), reverse=True)
    if rows_only:
        offset = _paper_library_page_number(params.get("offset"), 0, len(sorted_visible))
        limit = max(1, _paper_library_page_number(params.get("limit"), 100, 500))
        return {
            "papers": [_paper_library_list_item(paper) for paper in sorted_visible[offset:offset + limit]],
        }
    preview_limit = _paper_library_page_number(params.get("preview_limit"), 0, 500) if compact else 0
    if compact and preview_limit:
        library_papers = [
            _paper_library_list_item(paper) if index < preview_limit else _paper_library_tree_item(paper)
            for index, paper in enumerate(sorted_visible)
        ]
    elif compact:
        library_papers = [_paper_library_list_item(paper) for paper in sorted_visible]
    else:
        library_papers = sorted_visible
    return {
        "summary": {
            "paper_count": len(papers),
            "visible_count": len(visible),
            "analysis_count": analysis_count,
            "review_count": review_count,
            "data_root": str(DATA_ROOT),
            "paper_store_root": str(PAPER_STORE_ROOT),
            "semantic_search": semantic_summary,
            "paper_collection": collection_summary,
        },
        "papers": library_papers,
        "favorite_ids": favorites,
        "folders": _folder_tree(visible, include_papers=not compact),
        "filters": _filters(papers),
        "workflow": WORKFLOW_STEPS,
        "review_lenses": REVIEW_LENSES,
    }


def _http_get_text(url: str, *, timeout: int = 90) -> str:
    req = urllib.request.Request(str(url or ""), headers={"User-Agent": "TFIPortalPaper/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


def _ccf_cache_fresh(path: Path, max_age_seconds: int = 30 * 24 * 60 * 60) -> bool:
    try:
        return path.is_file() and _now() - int(path.stat().st_mtime) < max_age_seconds
    except Exception:
        return False


def _parse_ccf_catalog_html(html: str) -> list[dict]:
    venues: list[dict] = []
    rows = re.findall(r"<tr[^>]*class=[\"'][^\"']*\bitem\b[^\"']*[\"'][^>]*>(.*?)</tr>", str(html or ""), re.S | re.I)
    for row in rows:
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I)
        if len(cells) < 6:
            continue
        title_html = cells[2]
        rank = _strip_html(cells[3]).upper()
        kind = _strip_html(cells[4])
        if kind not in {"会议", "期刊"} or rank not in {"A", "B"}:
            continue
        href_match = re.search(r"<a[^>]+href=[\"']([^\"']+)[\"']", title_html, re.I)
        dblp_url = html_lib.unescape(href_match.group(1)).strip() if href_match else ""
        dblp_match = re.search(r"/db/(conf|journals)/([^/]+)/", dblp_url)
        if not dblp_match:
            continue
        dblp_url = dblp_url.replace("http://dblp.uni-trier.de", "https://dblp.org").replace("https://dblp.uni-trier.de", "https://dblp.org")
        abbr = _strip_html(cells[1])
        venues.append({
            "abbr": abbr,
            "short_name": abbr,
            "name": _strip_html(title_html),
            "rank": rank,
            "type": kind,
            "category": _strip_html(cells[5]),
            "dblp_url": dblp_url,
            "dblp_section": dblp_match.group(1).strip(),
            "dblp_conf_key": dblp_match.group(2).strip(),
            "source": "CCF recommended list v7 / 2026",
        })
    venues.sort(key=lambda item: (str(item.get("rank") or ""), str(item.get("category") or ""), str(item.get("abbr") or "")))
    return venues


def _load_ccf_catalog_cache() -> list[dict]:
    cached = _load_json(CCF_CATALOG_CACHE_PATH, {})
    venues = cached.get("venues") if isinstance(cached, dict) else []
    return [item for item in venues if isinstance(item, dict)] if isinstance(venues, list) else []


def _load_curated_venues() -> list[dict]:
    out = []
    for item in paper_state_store.curated_venues():
        if not isinstance(item, dict):
            continue
        conf_key = str(item.get("dblp_conf_key") or "").strip()
        section = str(item.get("dblp_section") or "conf").strip() or "conf"
        if not conf_key:
            continue
        venue = {
            **item,
            "abbr": str(item.get("abbr") or item.get("short_name") or conf_key).strip(),
            "short_name": str(item.get("short_name") or item.get("abbr") or conf_key).strip(),
            "name": str(item.get("name") or item.get("abbr") or conf_key).strip(),
            "rank": str(item.get("rank") or "Curated").strip() or "Curated",
            "category": str(item.get("category") or "芯片电路").strip() or "芯片电路",
            "type": str(item.get("type") or "会议").strip() or "会议",
            "dblp_section": section,
            "dblp_conf_key": conf_key,
            "dblp_url": str(item.get("dblp_url") or f"https://dblp.org/db/{section}/{conf_key}/").strip(),
            "source": str(item.get("source") or "TFI Portal curated chip/circuit venue list").strip(),
            "curated": True,
        }
        out.append(venue)
    return out


def _merge_curated_venues(venues: list[dict]) -> list[dict]:
    merged = [dict(item) for item in venues if isinstance(item, dict)]
    by_key = {str(item.get("dblp_conf_key") or ""): idx for idx, item in enumerate(merged)}
    for curated in _load_curated_venues():
        key = str(curated.get("dblp_conf_key") or "")
        if key and key in by_key:
            existing = dict(merged[by_key[key]])
            existing["curated"] = True
            existing["curated_category"] = str(curated.get("category") or "")
            existing["curated_reason"] = str(curated.get("curated_reason") or "")
            existing["curated_source"] = str(curated.get("source") or "")
            merged[by_key[key]] = existing
        else:
            merged.append(curated)
    merged.sort(key=lambda item: (str(item.get("rank") or ""), str(item.get("category") or ""), str(item.get("abbr") or "")))
    return merged


def _ccf_catalog(force: bool = False, *, allow_network: bool = True) -> list[dict]:
    if not force and _ccf_cache_fresh(CCF_CATALOG_CACHE_PATH):
        cached = _load_json(CCF_CATALOG_CACHE_PATH, {})
        venues = cached.get("venues") if isinstance(cached, dict) else []
        if (
            isinstance(venues, list)
            and all("short_name" in item for item in venues if isinstance(item, dict))
            and any(str(item.get("type") or "") == "期刊" for item in venues if isinstance(item, dict))
        ):
            return _merge_curated_venues([item for item in venues if isinstance(item, dict)])
    if not allow_network:
        return _merge_curated_venues(_load_ccf_catalog_cache())
    html = _http_get_text(CCF_CATALOG_URL, timeout=135)
    venues = _parse_ccf_catalog_html(html)
    CCF_CATALOG_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _write_json(CCF_CATALOG_CACHE_PATH, {
        "schema": "tfi.paper.ccf_catalog.v1",
        "source_url": CCF_CATALOG_URL,
        "source_version": "CCF recommended list v7 / 2026",
        "fetched_at": _now(),
        "venues": venues,
    })
    return _merge_curated_venues(venues)


def ccf_venues(params: dict | None = None) -> dict:
    params = params or {}
    rank = str(params.get("rank") or "").strip().upper()
    force = str(params.get("refresh") or "").strip().lower() in {"1", "true", "yes", "on"}
    venues = _ccf_catalog(force=force, allow_network=force)
    if rank in {"A", "B", "CURATED"}:
        target_rank = "Curated" if rank == "CURATED" else rank
        venues = [item for item in venues if str(item.get("rank") or "").upper() == target_rank.upper()]
    curated_count = sum(1 for item in venues if item.get("curated"))
    return {
        "summary": {
            "source_version": "CCF recommended list v7 / 2026 + TFI Portal curated chip/circuit venues",
            "venue_count": len(venues),
            "rank_a_count": sum(1 for item in venues if str(item.get("rank") or "").upper() == "A"),
            "rank_b_count": sum(1 for item in venues if str(item.get("rank") or "").upper() == "B"),
            "curated_count": curated_count,
        },
        "venues": venues,
    }


def _ccf_venue_by_key(conf_key: str) -> dict | None:
    key = str(conf_key or "").strip()
    return next((item for item in _ccf_catalog(allow_network=False) if str(item.get("dblp_conf_key") or "") == key), None) if key else None


def _ccf_cache_path(kind: str, conf_key: str, suffix: str = "") -> Path:
    tail = f"-{_slugify(suffix)}" if suffix else ""
    return CCF_DBLP_CACHE_ROOT / _slugify(conf_key) / f"{kind}{tail}.json"


def _ccf_entry_year(entry: dict | None) -> int:
    if not isinstance(entry, dict):
        return 0
    try:
        year = int(entry.get("year") or 0)
        if year:
            return year
    except (TypeError, ValueError):
        pass
    text = " ".join(str(entry.get(key) or "") for key in ("entry_id", "label", "xml_url"))
    match = re.search(r"\b((?:19|20)\d{2})\b", text)
    return int(match.group(1)) if match else 0


def _normalize_ccf_paper_ranks(value) -> set[str]:
    if value is None:
        return {"A"}
    if isinstance(value, str):
        tokens = re.split(r"[\s,;]+", value.strip())
    elif isinstance(value, (list, tuple, set)):
        tokens = [str(item or "") for item in value]
    else:
        tokens = [str(value or "")]
    ranks = {token.strip().upper() for token in tokens if token.strip()}
    if not ranks:
        return {"A"}
    if ranks & {"*", "ALL"}:
        return set()
    return {rank for rank in ranks if rank in {"A", "B", "CURATED"}}


def _dblp_entries_for_venue(venue: dict, *, force: bool = False, allow_network: bool = True) -> list[dict]:
    from management_portal import paper_dblp_metadata

    conf_key = str(venue.get("dblp_conf_key") or "").strip()
    cache_path = _ccf_cache_path("years", conf_key)
    if not force and cache_path.is_file():
        cached = _load_json(cache_path, {})
        years = cached.get("years") if isinstance(cached, dict) else []
        if isinstance(years, list) and years:
            return [item for item in years if isinstance(item, dict)]
    if not allow_network:
        return []
    years = paper_dblp_metadata.catalog_entries(venue)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(cache_path, {"fetched_at": _now(), "conf_key": conf_key, "section": venue.get("dblp_section") or "conf", "years": years})
    return years


def ccf_years(params: dict | None = None) -> dict:
    params = params or {}
    conf_key = str(params.get("venue") or params.get("conf") or "").strip()
    if not conf_key:
        raise ValueError("venue required")
    venue = _ccf_venue_by_key(conf_key)
    force = str(params.get("refresh") or "").strip().lower() in {"1", "true", "yes", "on"}
    if not venue and force:
        venue = next((item for item in _ccf_catalog(force=True, allow_network=True) if str(item.get("dblp_conf_key") or "") == conf_key), None)
    if not venue:
        raise KeyError("CCF venue not found")
    return {"venue": venue, "years": _dblp_entries_for_venue(venue, force=force, allow_network=force)}


def _paper_doi_from_url(value: str) -> str:
    match = re.search(r"doi\.org/(.+)$", str(value or ""), re.I)
    return match.group(1).strip() if match else ""


def _registered_paper_index(papers: list[dict]) -> dict:
    index = {"tokens": {}, "titles": {}, "title_ngrams": {}, "titles_by_id": {}}
    for paper in papers:
        if not isinstance(paper, dict):
            continue
        paper_id = str(paper.get("id") or "")
        if not paper_id:
            continue
        identifiers = paper.get("identifiers") if isinstance(paper.get("identifiers"), dict) else {}
        fields = [paper.get(key) for key in ("id", "slug", "title", "pdf_path", "analysis_path")]
        fields.extend(identifiers.values())
        values = [str(value or "").strip() for value in fields if str(value or "").strip()]
        title_key = re.sub(r"\W+", "", str(paper.get("title") or "").casefold())
        if title_key:
            index["titles"].setdefault(title_key, paper_id)
            index["titles_by_id"][paper_id] = title_key
            if len(title_key) >= 24:
                for offset in range(len(title_key) - 23):
                    gram = title_key[offset:offset + 24]
                    index["title_ngrams"].setdefault(gram, set()).add(paper_id)
        for value in values:
            token = value.casefold()
            index["tokens"].setdefault(token, paper_id)
            doi = _paper_doi_from_url(value)
            if doi:
                index["tokens"].setdefault(doi.casefold(), paper_id)
    return index


def _registered_paper_for_dblp(item: dict, registered_index: dict) -> str:
    title = re.sub(r"\W+", "", str(item.get("title") or "").casefold())
    doi = str(item.get("doi") or "").casefold()
    dblp_key = str(item.get("dblp_key") or "").casefold()
    url = str(item.get("dblp_url") or "").casefold()
    tokens = registered_index.get("tokens") if isinstance(registered_index, dict) else {}
    for token in (doi, dblp_key, url):
        if token and token in tokens:
            return str(tokens[token] or "")
    exact = registered_index.get("titles", {}).get(title)
    if exact:
        return str(exact)
    candidate_ids = set()
    if len(title) >= 24:
        title_ngrams = registered_index.get("title_ngrams", {})
        for offset in range(len(title) - 23):
            candidate_ids.update(title_ngrams.get(title[offset:offset + 24], ()))
    titles_by_id = registered_index.get("titles_by_id", {})
    for paper_id in candidate_ids:
        ptitle = str(titles_by_id.get(paper_id) or "")
        shorter = min(len(title), len(ptitle)) if title and ptitle else 0
        longer = max(len(title), len(ptitle)) if title and ptitle else 0
        near_contained = (
            shorter >= 40
            and longer > 0
            and shorter / longer >= 0.75
            and (title in ptitle or ptitle in title)
        )
        if title and ptitle and (title == ptitle or near_contained):
            return str(paper_id or "")
    return ""


def _ccf_paper_identity(paper: dict) -> str:
    parts = [
        str(paper.get("dblp_key") or "").strip(),
        str(paper.get("doi") or "").strip(),
        str(paper.get("dblp_url") or "").strip(),
        str(paper.get("ee") or "").strip(),
        str(paper.get("title") or "").strip(),
    ]
    return "\n".join(part for part in parts if part).casefold()


def _ccf_enrichment_cache_path(paper: dict) -> Path:
    title = str(paper.get("title") or "paper").strip() or "paper"
    digest = hashlib.sha1(_ccf_paper_identity(paper).encode("utf-8", errors="ignore")).hexdigest()[:14]
    return CCF_ENRICHMENT_CACHE_ROOT / f"{_slugify(title)[:70]}-{digest}.json"


def _write_ccf_enrichment_state(
    cache_path: Path,
    payload: dict,
    *,
    actor: object = "",
) -> None:
    _write_json(cache_path, payload)
    _notify_paper_projection(
        cache_path.stem,
        fields=["ccf_enrichment"],
    )


def _read_ccf_enrichment(paper: dict) -> dict | None:
    path = _ccf_enrichment_cache_path(paper)
    data = _load_json(path, {})
    return data if isinstance(data, dict) and data else None


def _abstract_from_openalex_inverted_index(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    positioned: list[tuple[int, str]] = []
    for word, indexes in value.items():
        if not isinstance(indexes, list):
            continue
        for index in indexes:
            try:
                positioned.append((int(index), str(word)))
            except Exception:
                continue
    return " ".join(word for _, word in sorted(positioned)).strip()


def _title_similarity(a: str, b: str) -> float:
    def words(text: str) -> set[str]:
        return {item for item in re.findall(r"[a-z0-9]{3,}", str(text or "").casefold())}

    left = words(a)
    right = words(b)
    if not left or not right:
        return 0.0
    if re.sub(r"\W+", "", str(a).casefold()) == re.sub(r"\W+", "", str(b).casefold()):
        return 1.0
    return len(left & right) / max(1, len(left | right))


def _fetch_openalex_ccf_candidate(paper: dict) -> dict | None:
    title = str(paper.get("title") or "").strip()
    doi = str(paper.get("doi") or "").strip()
    urls = []
    if doi:
        urls.append("https://api.openalex.org/works?" + urlencode({"filter": f"doi:{doi}", "per-page": "3"}))
    if title:
        urls.append("https://api.openalex.org/works?" + urlencode({"search": title, "per-page": "5"}))
    for url in urls:
        try:
            payload = json.loads(_http_get_text(url, timeout=48))
        except Exception:
            continue
        results = payload.get("results") if isinstance(payload, dict) else []
        for item in results if isinstance(results, list) else []:
            if not isinstance(item, dict):
                continue
            item_title = str(item.get("title") or "").strip()
            if title and item_title and _title_similarity(title, item_title) < 0.45:
                continue
            authorships = item.get("authorships") if isinstance(item.get("authorships"), list) else []
            authors = []
            for authorship in authorships[:12]:
                author = authorship.get("author") if isinstance(authorship, dict) else {}
                name = str((author or {}).get("display_name") or "").strip()
                if name:
                    authors.append(name)
            abstract = _abstract_from_openalex_inverted_index(item.get("abstract_inverted_index"))
            return {
                "source": "OpenAlex",
                "title": item_title,
                "abstract": abstract,
                "year": item.get("publication_year") or "",
                "venue": ((item.get("primary_location") or {}).get("source") or {}).get("display_name") if isinstance(item.get("primary_location"), dict) else "",
                "url": item.get("id") or item.get("doi") or "",
                "authors": ", ".join(authors),
            }
    return None


def _fetch_semantic_scholar_ccf_candidate(paper: dict) -> dict | None:
    title = str(paper.get("title") or "").strip()
    if not title:
        return None
    url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urlencode({
        "query": title,
        "limit": "5",
        "fields": "title,abstract,authors,year,venue,url,externalIds",
    })
    try:
        payload = json.loads(_http_get_text(url, timeout=48))
    except Exception:
        return None
    results = payload.get("data") if isinstance(payload, dict) else []
    for item in results if isinstance(results, list) else []:
        if not isinstance(item, dict):
            continue
        item_title = str(item.get("title") or "").strip()
        if item_title and _title_similarity(title, item_title) < 0.45:
            continue
        authors = item.get("authors") if isinstance(item.get("authors"), list) else []
        return {
            "source": "Semantic Scholar",
            "title": item_title,
            "abstract": str(item.get("abstract") or "").strip(),
            "year": item.get("year") or "",
            "venue": item.get("venue") or "",
            "url": item.get("url") or "",
            "authors": ", ".join(str(author.get("name") or "").strip() for author in authors[:12] if isinstance(author, dict) and author.get("name")),
        }
    return None


def _fetch_ccf_enrichment_sources(paper: dict) -> list[dict]:
    candidates = []
    for fetcher in (_fetch_openalex_ccf_candidate, _fetch_semantic_scholar_ccf_candidate):
        item = fetcher(paper)
        if item and any(str(item.get(key) or "").strip() for key in ("abstract", "url", "venue")):
            candidates.append(item)
    seen = set()
    unique = []
    for item in candidates:
        key = (str(item.get("source") or ""), str(item.get("title") or ""), str(item.get("url") or ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _generate_ccf_enrichment_summary(paper: dict, candidates: list[dict], *, actor: str) -> tuple[str, dict]:
    source_lines = []
    for item in candidates:
        source_lines.append(
            f"Source: {item.get('source') or ''}\n"
            f"Title: {item.get('title') or ''}\n"
            f"Venue/Year: {item.get('venue') or ''} {item.get('year') or ''}\n"
            f"URL: {item.get('url') or ''}\n"
            f"Abstract: {item.get('abstract') or ''}"
        )
    prompt = f"""请用中文写一段 80-180 字总结，说明这篇论文大概做什么、核心问题和可能的方法方向。不要使用 Markdown、标题或列表。如果检索材料缺少摘要，只能根据标题和来源做保守概括，并明确“不确定细节需要读原文确认”。

DBLP metadata:
Title: {paper.get('title') or ''}
Authors: {paper.get('authors') or ''}
Venue/Year: {paper.get('venue') or paper.get('booktitle') or ''} {paper.get('year') or ''}
DOI: {paper.get('doi') or ''}

Online metadata:
{chr(10).join(source_lines) if source_lines else 'No online metadata found.'}
"""
    content, usage = _resident_reply(prompt, actor=actor, resident_scope="paper:ccf")
    return _clean_summary_paragraph(content), usage


def _parse_json_object_from_text(text: str) -> dict:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("empty JSON text")
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        parsed = json.loads(match.group(0))
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("no JSON object found")


def _generate_ccf_web_investigation(paper: dict, candidates: list[dict], *, actor: str) -> dict:
    clean_sources = []
    source_lines = []
    for item in candidates[:8]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        abstract = str(item.get("abstract") or "").strip()
        source = str(item.get("source") or "").strip()
        venue = str(item.get("venue") or "").strip()
        year = str(item.get("year") or "").strip()
        if not any((title, url, abstract, venue)):
            continue
        clean_sources.append({
            "title": title or url or source,
            "url": url,
            "type": source or "metadata",
            "note": "metadata source",
        })
        source_lines.append(
            f"Source: {source}\n"
            f"Title: {title}\n"
            f"Venue/Year: {venue} {year}\n"
            f"URL: {url}\n"
            f"Abstract: {abstract}"
        )
    if not source_lines:
        return {
            "summary_zh": "当前只从 DBLP 找到题名、作者和发表信息，未找到可用于进一步概括的摘要或开放元数据。",
            "findings": ["没有调用 AI；本次结果只基于本地 DBLP 条目和可用公开元数据。"],
            "sources": [],
            "confidence": "low",
            "usage": {"source": "metadata-only"},
        }
    prompt = f"""请根据下面的 DBLP 条目和在线元数据，返回 JSON 对象，不要 Markdown。

JSON schema:
{{
  "summary_zh": "中文一段话，120-260 字，说明论文大概做什么、核心问题和可能方法。",
  "findings": ["2-5 条中文要点，只能来自给定元数据"],
  "confidence": "low|medium|high"
}}

要求：
- 不要调用外部搜索，不要声称已经阅读原文。
- 如果材料只有题名或摘要不足，明确写出不确定性。
- 不要把相似标题的其他论文混进来。

DBLP metadata:
Title: {paper.get('title') or ''}
Authors: {paper.get('authors') or ''}
Venue/Year: {paper.get('venue') or paper.get('booktitle') or ''} {paper.get('year') or ''}
DOI: {paper.get('doi') or ''}

Online metadata:
{chr(10).join(source_lines)}
"""
    content, usage = _resident_reply(prompt, actor=actor, resident_scope="paper:ccf")
    parsed = _parse_json_object_from_text(content)
    findings = parsed.get("findings") if isinstance(parsed.get("findings"), list) else []
    return {
        "summary_zh": _clean_summary_paragraph(str(parsed.get("summary_zh") or "")),
        "findings": [str(item).strip() for item in findings if str(item or "").strip()][:8],
        "sources": clean_sources,
        "confidence": str(parsed.get("confidence") or "").strip(),
        "usage": {"source": "kimi_metadata", **(usage if isinstance(usage, dict) else {})},
    }


def _run_ccf_enrichment_job(cache_path: Path, paper: dict, actor: str = "", *, include_web: bool = False) -> None:
    try:
        current = _load_json(cache_path, {})
        _write_ccf_enrichment_state(cache_path, {
            **(current if isinstance(current, dict) else {}),
            "status": "running",
            "updated_at": _now(),
        }, actor=actor)
        candidates = _fetch_ccf_enrichment_sources(paper)
        abstract = next((str(item.get("abstract") or "").strip() for item in candidates if str(item.get("abstract") or "").strip()), "")
        summary, usage = _generate_ccf_enrichment_summary(paper, candidates, actor=actor)
        fast_payload = {
            "status": "web_pending" if include_web else "done",
            "web_status": "pending" if include_web else "",
            "updated_at": _now(),
            "actor": actor,
            "paper_key": _ccf_paper_identity(paper),
            "abstract": abstract,
            "summary_zh": summary,
            "sources": candidates,
            "usage": usage,
        }
        current_request = _load_json(cache_path, {})
        fast_payload = {
            **(
                current_request
                if isinstance(current_request, dict)
                else {}
            ),
            **fast_payload,
        }
        web_requested = include_web or bool((current_request if isinstance(current_request, dict) else {}).get("requested_web"))
        if web_requested:
            fast_payload["status"] = "web_pending"
            fast_payload["web_status"] = "pending"
            fast_payload["requested_web"] = True
        _write_ccf_enrichment_state(cache_path, fast_payload, actor=actor)
        if not web_requested:
            return
        try:
            web = _generate_ccf_web_investigation(paper, candidates, actor=actor)
            final_payload = {
                **fast_payload,
                "status": "done",
                "web_status": "done",
                "requested_web": False,
                "updated_at": _now(),
                "web_summary_zh": str(web.get("summary_zh") or "").strip(),
                "web_findings": web.get("findings") if isinstance(web.get("findings"), list) else [],
                "web_sources": web.get("sources") if isinstance(web.get("sources"), list) else [],
                "web_confidence": str(web.get("confidence") or "").strip(),
                "web_usage": web.get("usage") if isinstance(web.get("usage"), dict) else {},
            }
            _write_ccf_enrichment_state(cache_path, final_payload, actor=actor)
        except Exception as exc:
            error_payload = {
                **fast_payload,
                "status": "done",
                "web_status": "error",
                "requested_web": False,
                "updated_at": _now(),
                "web_error": str(exc),
            }
            _write_ccf_enrichment_state(cache_path, error_payload, actor=actor)
    except Exception as exc:
        current = _load_json(cache_path, {})
        current.update({
            "status": "error",
            "updated_at": _now(),
            "error": str(exc),
            "paper_key": _ccf_paper_identity(paper),
        })
        _write_ccf_enrichment_state(cache_path, current, actor=actor)


def _run_ccf_web_enrichment_job(cache_path: Path, paper: dict, actor: str = "") -> None:
    current = _load_json(cache_path, {})
    if not (
        str(current.get("summary_zh") or "").strip()
        or str(current.get("abstract") or "").strip()
    ):
        _run_ccf_enrichment_job(
            cache_path,
            paper,
            actor,
            include_web=True,
        )
        return
    candidates = (
        current.get("sources")
        if isinstance(current.get("sources"), list)
        else []
    )
    pending = {
        **current,
        "status": "web_pending",
        "web_status": "pending",
        "updated_at": _now(),
        "actor": actor,
        "paper_key": _ccf_paper_identity(paper),
    }
    _write_ccf_enrichment_state(cache_path, pending, actor=actor)
    try:
        web = _generate_ccf_web_investigation(paper, candidates, actor=actor)
        final_payload = {
            **pending,
            "status": "done",
            "web_status": "done",
            "requested_web": False,
            "updated_at": _now(),
            "web_summary_zh": str(web.get("summary_zh") or "").strip(),
            "web_findings": (
                web.get("findings")
                if isinstance(web.get("findings"), list)
                else []
            ),
            "web_sources": (
                web.get("sources")
                if isinstance(web.get("sources"), list)
                else []
            ),
            "web_confidence": str(
                web.get("confidence") or ""
            ).strip(),
            "web_usage": (
                web.get("usage")
                if isinstance(web.get("usage"), dict)
                else {}
            ),
        }
        _write_ccf_enrichment_state(cache_path, final_payload, actor=actor)
    except Exception as exc:
        error_payload = {
            **pending,
            "status": "done",
            "web_status": "error",
            "requested_web": False,
            "updated_at": _now(),
            "web_error": str(exc),
        }
        _write_ccf_enrichment_state(cache_path, error_payload, actor=actor)


def _ccf_enrichment_request_path(cache_path: Path) -> Path:
    return cache_path.with_suffix(".request.json")


def _run_ccf_enrichment_request(request_path: Path) -> None:
    request_path = Path(request_path).resolve(strict=False)
    root = CCF_ENRICHMENT_CACHE_ROOT.resolve(strict=False)
    if root not in request_path.parents:
        raise ValueError("CCF enrichment request is outside its work root")
    request = _load_json(request_path, {})
    paper = (
        dict(request.get("paper"))
        if isinstance(request.get("paper"), dict)
        else {}
    )
    if not str(paper.get("title") or "").strip():
        raise ValueError("CCF enrichment request has no paper")
    cache_path = Path(str(request.get("cache_path") or "")).resolve(
        strict=False
    )
    expected = _ccf_enrichment_cache_path(paper).resolve(strict=False)
    if cache_path != expected:
        raise ValueError("CCF enrichment request cache identity mismatch")
    actor = str(request.get("actor") or "")
    if bool(request.get("web_only")):
        _run_ccf_web_enrichment_job(cache_path, paper, actor)
    else:
        _run_ccf_enrichment_job(
            cache_path,
            paper,
            actor,
            include_web=bool(request.get("include_web")),
        )


def _start_ccf_enrichment_task(
    cache_path: Path,
    paper: dict,
    actor: str,
    *,
    include_web: bool,
    web_only: bool,
) -> dict:
    from management_portal import business_tasks

    request_path = _ccf_enrichment_request_path(cache_path)
    _write_json(request_path, {
        "cache_path": str(cache_path),
        "paper": dict(paper),
        "actor": str(actor or ""),
        "include_web": bool(include_web),
        "web_only": bool(web_only),
        "created_at": _now(),
    })
    task_id = f"paper-ccf:{_ccf_paper_identity(paper)[:160]}"
    business_tasks.start(
        task_id,
        _run_ccf_enrichment_request,
        request_path,
    )
    return {"business_task_id": task_id}


def _ccf_enrichment_task_active(cached: dict) -> bool:
    from management_portal import business_tasks

    return business_tasks.is_active(
        str(cached.get("business_task_id") or "")
    )


def ccf_paper_enrichment(payload: dict, actor: str = "") -> dict:
    paper = payload.get("paper") if isinstance(payload.get("paper"), dict) else payload
    if not isinstance(paper, dict) or not str(paper.get("title") or "").strip():
        raise ValueError("paper title required")
    want_web = str(payload.get("web") or "").strip().lower() in {"1", "true", "yes", "on"} if isinstance(payload, dict) else False
    cache_path = _ccf_enrichment_cache_path(paper)
    lock_path = cache_path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file, lock_stream(lock_file):
        cached = _load_json(cache_path, {})
        status = str(cached.get("status") or "").strip()
        web_status = str(cached.get("web_status") or "").strip()
        if not want_web and status == "done":
            return cached
        if want_web and status == "done" and web_status == "done":
            return cached
        stale = (
            _now()
            - int(
                cached.get("updated_at")
                or cached.get("started_at")
                or 0
            )
            > 20 * 60
        )
        active_statuses = {"pending", "running", "web_pending"}
        if status in active_statuses and (
            not stale or _ccf_enrichment_task_active(cached)
        ):
            if not want_web:
                return cached
            pending = {
                **(cached if isinstance(cached, dict) else {}),
                "status": "web_pending",
                "web_status": "pending",
                "requested_web": True,
                "updated_at": _now(),
                "actor": actor,
                "paper_key": _ccf_paper_identity(paper),
            }
            _write_ccf_enrichment_state(cache_path, pending, actor=actor)
            return pending
        pending = {
            **(cached if isinstance(cached, dict) else {}),
            "status": (
                "web_pending"
                if want_web and bool(cached)
                else "pending"
            ),
            "web_status": "pending" if want_web else web_status,
            "requested_web": bool(want_web),
            "started_at": _now(),
            "updated_at": _now(),
            "actor": actor,
            "paper_key": _ccf_paper_identity(paper),
            "error": "",
        }
        _write_ccf_enrichment_state(cache_path, pending, actor=actor)
        try:
            execution = _start_ccf_enrichment_task(
                cache_path,
                dict(paper),
                actor,
                include_web=want_web,
                web_only=bool(
                    want_web
                    and (
                        str(cached.get("summary_zh") or "").strip()
                        or str(cached.get("abstract") or "").strip()
                    )
                ),
            )
        except Exception as exc:
            failed = {
                **pending,
                "status": "error",
                "updated_at": _now(),
                "error": (
                    "Failed to start CCF enrichment business task: "
                    f"{exc}"
                ),
            }
            _write_ccf_enrichment_state(cache_path, failed, actor=actor)
            return failed
        accepted = {**pending, **execution}
        _write_ccf_enrichment_state(cache_path, accepted, actor=actor)
        return accepted


def ccf_papers_cache(params: dict | None = None) -> dict:
    params = params or {}
    conf_key = str(params.get("venue") or params.get("conf") or "").strip()
    year_text = str(params.get("year") or "").strip()
    if not conf_key or not year_text:
        raise ValueError("venue and year required")
    venue = _ccf_venue_by_key(conf_key)
    force = str(params.get("refresh") or "").strip().lower() in {"1", "true", "yes", "on"}
    if not venue and force:
        venue = next((item for item in _ccf_catalog(force=True, allow_network=True) if str(item.get("dblp_conf_key") or "") == conf_key), None)
    if not venue:
        raise KeyError("CCF venue not found")
    cache_path = _ccf_cache_path("papers", conf_key, year_text)
    papers = None
    entry = None
    if not force and _ccf_cache_fresh(cache_path):
        cached = _load_json(cache_path, {})
        papers = cached.get("papers") if isinstance(cached, dict) else None
        entry = cached.get("entry") if isinstance(cached, dict) and isinstance(cached.get("entry"), dict) else None
    if isinstance(papers, list) and entry is None:
        years = _dblp_entries_for_venue(venue, allow_network=False)
        entry = next((
            item for item in years
            if str(item.get("entry_id") or "") == year_text
            or str(item.get("year") or "") == year_text
            or str(item.get("issue") or "") == year_text
        ), None)
    if not isinstance(papers, list):
        if not force:
            years = _dblp_entries_for_venue(venue, allow_network=False)
            entry = next((
                item for item in years
                if str(item.get("entry_id") or "") == year_text
                or str(item.get("year") or "") == year_text
                or str(item.get("issue") or "") == year_text
            ), None)
            return {
                "venue": venue,
                "year": int((entry or {}).get("year") or 0),
                "issue": str((entry or {}).get("issue") or ""),
                "entry_id": str((entry or {}).get("entry_id") or year_text),
                "label": str((entry or {}).get("label") or year_text),
                "count": 0,
                "papers": [],
                "cache_missing": True,
            }
        years = _dblp_entries_for_venue(venue, force=True, allow_network=True)
        entry = next((
            item for item in years
            if str(item.get("entry_id") or "") == year_text
            or str(item.get("year") or "") == year_text
            or str(item.get("issue") or "") == year_text
        ), None)
        if not entry:
            raise KeyError("CCF venue year or issue not found")
        year = int(entry.get("year") or 0)
        if not year:
            year_match = re.search(r"\b((?:19|20)\d{2})\b", str(entry.get("label") or year_text))
            year = int(year_match.group(1)) if year_match else datetime.now().year
        from management_portal import paper_dblp_metadata

        papers = paper_dblp_metadata.papers_for_entry(venue, entry)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(cache_path, {"fetched_at": _now(), "venue": venue, "entry": entry, "year": year, "metadata_source": "dblp-sparql", "papers": papers})
    return {
        "venue": venue,
        "year": int((entry or {}).get("year") or 0),
        "issue": str((entry or {}).get("issue") or ""),
        "entry_id": str((entry or {}).get("entry_id") or year_text),
        "label": str((entry or {}).get("label") or year_text),
        "count": len(papers),
        "papers": papers,
    }


def ccf_papers(params: dict | None = None) -> dict:
    """Add Portal registration and enrichment only for the interactive page."""
    result = ccf_papers_cache(params)
    if result.get("cache_missing"):
        return result
    papers = result["papers"]
    registered_index = _registered_paper_index(resource_index_papers())
    public_with_order = []
    for order, item in enumerate(papers):
        if isinstance(item, dict):
            registered_id = _registered_paper_for_dblp(item, registered_index)
            public_with_order.append((0 if registered_id else 1, order, {
                **item,
                "registered_paper_id": registered_id,
                "registered": bool(registered_id),
                "enrichment": _read_ccf_enrichment(item) or {},
            }))
    public = [item for _, _, item in sorted(public_with_order, key=lambda row: (row[0], row[1]))]
    return {**result, "count": len(public), "papers": public}


def register_ccf_dblp_paper(payload: dict, actor: str) -> dict:
    paper = payload.get("paper") if isinstance(payload.get("paper"), dict) else payload
    title = str(paper.get("title") or "").strip()
    if not title:
        raise ValueError("title required")
    tags = _normalize_list(["ccf", f"CCF-{str(paper.get('ccf_rank') or '').upper()}", str(paper.get("ccf_category") or "")])
    abstract_lines = [
        str(paper.get("abstract") or "").strip(),
        f"DBLP: {paper.get('dblp_url') or ''}".strip(),
        f"DOI: {paper.get('doi') or ''}".strip(),
        f"CCF: {paper.get('ccf_rank') or ''} {paper.get('ccf_category') or ''}".strip(),
    ]
    return register_paper({
        "title": title,
        "authors": str(paper.get("authors") or ""),
        "venue": str(paper.get("venue") or paper.get("booktitle") or ""),
        "year": str(paper.get("year") or ""),
        "abstract": "\n".join(line for line in abstract_lines if line and not line.endswith(":")),
        "tags": tags,
        "project_ids": payload.get("project_ids") or [],
        "session": str(payload.get("session") or ""),
        "status": "metadata-only",
        "identifiers": {
            "doi": str(paper.get("doi") or ""),
            "dblp": str(paper.get("dblp_key") or ""),
            "dblp_url": str(paper.get("dblp_url") or ""),
        },
    }, actor)


def refresh_ccf_cache(
    *,
    latest_years: int | None = 2,
    min_paper_year: int | None = 2020,
    paper_ranks: str | list[str] | tuple[str, ...] | set[str] | None = "A,Curated",
    max_venues: int | None = None,
    delay_seconds: float = 0.0,
    refresh_catalog: bool = True,
    refresh_existing: bool = True,
) -> dict:
    latest_years = normalize_optional_limit(latest_years, default=2)
    min_paper_year = normalize_optional_limit(
        min_paper_year,
        default=2020,
    )
    auto_paper_ranks = _normalize_ccf_paper_ranks(paper_ranks)
    max_venues = normalize_optional_limit(max_venues)
    delay_seconds = max(0.0, float(delay_seconds or 0.0))
    started_at = _now()
    venues = _ccf_catalog(force=refresh_catalog, allow_network=refresh_catalog)
    venues = sorted(
        venues,
        key=lambda item: (
            0 if str(item.get("dblp_section") or "conf") == "conf" else 1,
            str(item.get("rank") or ""),
            str(item.get("abbr") or item.get("dblp_conf_key") or ""),
        ),
    )
    if max_venues is not None:
        venues = venues[:max_venues]
    report = {
        "schema": "tfi.paper.ccf_refresh.v1",
        "started_at": started_at,
        "finished_at": None,
        "latest_years": latest_years,
        "all_entries": latest_years is None,
        "min_paper_year": min_paper_year,
        "paper_ranks": sorted(auto_paper_ranks) if auto_paper_ranks else ["all"],
        "refresh_catalog": refresh_catalog,
        "refresh_existing": refresh_existing,
        "venue_count": len(venues),
        "year_index_count": 0,
        "paper_year_count": 0,
        "paper_count": 0,
        "skipped_by_rank": 0,
        "skipped_before_min_year": 0,
        "errors": [],
    }
    CCF_REFRESH_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _write_json(CCF_REFRESH_REPORT_PATH, report)
    for venue in venues:
        conf_key = str(venue.get("dblp_conf_key") or "").strip()
        if not conf_key:
            continue
        try:
            years = _dblp_entries_for_venue(venue, force=refresh_existing, allow_network=True)
            report["year_index_count"] += len(years)
        except Exception as exc:
            report["errors"].append({"venue": conf_key, "stage": "years", "error": str(exc)})
            continue
        selected_entries = (
            years if latest_years is None else years[:latest_years]
        )
        venue_rank = str(venue.get("rank") or "").strip().upper()
        if auto_paper_ranks and venue_rank not in auto_paper_ranks:
            report["skipped_by_rank"] += len(selected_entries)
            CCF_REFRESH_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
            _write_json(CCF_REFRESH_REPORT_PATH, report)
            continue
        for year_entry in selected_entries:
            entry_id = str(year_entry.get("entry_id") or year_entry.get("year") or year_entry.get("issue") or "").strip()
            if not entry_id:
                continue
            entry_year = _ccf_entry_year(year_entry)
            if (
                min_paper_year is not None
                and (not entry_year or entry_year < min_paper_year)
            ):
                report["skipped_before_min_year"] += 1
                continue
            try:
                cached_payload = ccf_papers_cache({"venue": conf_key, "year": entry_id})
                if cached_payload.get("cache_missing") or refresh_existing:
                    payload = ccf_papers_cache({"venue": conf_key, "year": entry_id, "refresh": "1"})
                else:
                    payload = cached_payload
                report["paper_year_count"] += 1
                report["paper_count"] += int(payload.get("count") or 0)
            except Exception as exc:
                report["errors"].append({"venue": conf_key, "entry_id": entry_id, "stage": "papers", "error": str(exc)})
            if delay_seconds:
                time.sleep(delay_seconds)
            CCF_REFRESH_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
            _write_json(CCF_REFRESH_REPORT_PATH, report)
        CCF_REFRESH_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _write_json(CCF_REFRESH_REPORT_PATH, report)
    report["finished_at"] = _now()
    CCF_REFRESH_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _write_json(CCF_REFRESH_REPORT_PATH, report)
    return report


PAPER_DETAIL_SECTIONS = {"analysis", "summary", "exp", "reviews", "notes", "refs", "figures"}


def _paper_detail_sections(sections: object = None) -> set[str]:
    if sections is None or str(sections).strip() == "":
        return set(PAPER_DETAIL_SECTIONS)
    if isinstance(sections, str):
        values = re.split(r"[\s,]+", sections)
    elif isinstance(sections, (list, tuple, set)):
        values = [str(item or "") for item in sections]
    else:
        values = [str(sections)]
    return {value.strip().casefold() for value in values if value.strip().casefold() in PAPER_DETAIL_SECTIONS}


def get_paper_detail(paper_id: str, sections: object = None) -> dict:
    paper_id = str(paper_id or "").strip()
    requested = _paper_detail_sections(sections)
    with LOCK:
        paper = _paper_by_id(paper_id)
        if not paper:
            raise KeyError("paper not found")
        reviews = paper_state_store.records("reviews", paper_id=paper["id"]) if "reviews" in requested else []
        notes = paper_state_store.records("notes", paper_id=paper["id"]) if "notes" in requested else []
        refs = _refs_for_paper(paper) if "refs" in requested else []
        figures = _load_figures(paper) if "figures" in requested else []
    if "figures" in requested:
        _recover_figure_tasks(paper)
        figures = _load_figures(paper)
    public = _paper_public(paper)
    analysis_source = str(paper.get("analysis_path") or "").strip()
    result = {
        "paper": {
            **public,
            "pdf_url": f"/paper/api/asset?id={paper['id']}&kind=pdf" if public["has_pdf"] else "",
            "analysis_url": analysis_source if _is_url(analysis_source) else "",
        },
        "loaded_sections": sorted(requested),
        "assets_job": _latest_paper_run(str(paper.get("id") or ""), kind="assets"),
    }
    if "analysis" in requested:
        analysis_path = _safe_file_path(analysis_source)
        analysis_text = _read_text_preview(analysis_path)
        result["analysis"] = {
            "content": analysis_text,
            "headings": [
                {"level": len(match.group(1)), "title": match.group(2).strip()[:120]}
                for match in re.finditer(r"^(#{1,4})\s+(.+)$", analysis_text, re.MULTILINE)
            ][:40],
            "path": str(analysis_path or analysis_source),
        }
    if "summary" in requested:
        summary_path = _paper_store_file(paper, "summary")
        result["summary"] = {"content": _summary_text(paper), "path": str(summary_path or "")}
    if "exp" in requested:
        exp_path = _paper_store_file(paper, "exp")
        result["exp"] = {
            "content": _read_text_preview(exp_path, limit=120_000).strip() if exp_path and exp_path.is_file() else "",
            "path": str(exp_path or ""),
        }
    if "reviews" in requested:
        result["reviews"] = sorted(reviews, key=lambda item: int(item.get("created_at") or 0), reverse=True)
    if "notes" in requested:
        result["notes"] = sorted(notes, key=lambda item: int(item.get("created_at") or 0), reverse=True)
    if "refs" in requested:
        result["refs"] = sorted(refs, key=lambda item: int(item.get("index") or 0))
    if "figures" in requested:
        result["figures"] = [_figure_public(item, paper["id"]) for item in sorted(figures, key=lambda item: int(item.get("index") or 0))]
    return result


def register_paper(payload: dict, actor: str) -> dict:
    clean_payload = dict(payload)
    upload_id = str(clean_payload.pop("registration_upload_id", "") or "").strip()
    draft = _paper_registration_draft(upload_id, actor=actor) if upload_id else {}
    staged_pdf = _paper_registration_draft_pdf(upload_id) if upload_id else None
    if staged_pdf and not staged_pdf.is_file():
        raise FileNotFoundError("uploaded registration PDF not found")
    draft_metadata = draft.get("metadata") if isinstance(draft.get("metadata"), dict) else {}
    for key in (
        "title", "authors", "venue", "year", "session", "abstract", "tags", "workload",
        "platform", "architecture", "optimization", "doi", "arxiv",
    ):
        # Only omitted fields inherit extraction defaults. An explicit empty
        # value is a user correction, including while extraction is running.
        if key not in clean_payload:
            clean_payload[key] = draft_metadata.get(key)
    if staged_pdf:
        clean_payload["pdf_path"] = str(staged_pdf)
    pdf_source = str(clean_payload.get("pdf_path") or "").strip()
    if pdf_source and not _is_url(pdf_source):
        source = _safe_file_path(pdf_source)
        if not source or not source.is_file():
            raise ValueError("registration PDF is unavailable; upload the PDF again")
        # Reject an unreadable local source before creating the Paper or
        # consuming its draft. Remote downloads retain their pending path.
        try:
            with source.open("rb") as stream:
                stream.read(1)
        except OSError as exc:
            raise ValueError("registration PDF is unreadable; upload the PDF again") from exc
    with LOCK:
        reused = bool(PAPER_STORE.find_existing_id(clean_payload))
        paper = PAPER_STORE.register(clean_payload, actor)
        paper_id = str(paper.get("id") or "")
        current_pdf = _paper_store_file(paper, "pdf")
        if staged_pdf and (not current_pdf or not current_pdf.is_file()):
            found = PAPER_STORE.manifest_for_paper_id(paper_id)
            if not found:
                raise KeyError("paper store manifest not found")
            manifest_path, manifest = found
            paper_dir = PAPER_STORE.paper_dir_from_manifest(manifest, manifest_path)
            paper_dir.mkdir(parents=True, exist_ok=True)
            target = paper_dir / "paper.pdf"
            shutil.copy2(staged_pdf, target)
            paper = PAPER_STORE.update_paper(
                paper_id,
                {"pdf_path": str(target), "status": "registered"},
            ) or paper
    if upload_id:
        shutil.rmtree(_paper_registration_draft_dir(upload_id), ignore_errors=True)
    if not reused or staged_pdf:
        # Registering a paper the library already holds only merges its relations
        # (e.g. relating it to another Project); its summary and assets stay.
        _start_summary_task(str(paper.get("id") or ""), actor=actor)
        _start_assets_task(str(paper.get("id") or ""), actor=actor)
    return get_paper_detail(paper["id"])


def _validated_pdf_upload(payload: dict) -> tuple[bytes, str]:
    data = payload.get("data_bytes")
    filename = str(payload.get("filename") or "paper.pdf").strip()
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("pdf file required")
    if len(data) > PAPER_PDF_UPLOAD_MAX_BYTES:
        raise ValueError("pdf file is too large")
    if filename and not filename.lower().endswith(".pdf"):
        raise ValueError("uploaded file must be a PDF")
    pdf = bytes(data)
    if not pdf[:5].startswith(b"%PDF"):
        raise ValueError("uploaded file does not look like a PDF")
    return pdf, filename or "paper.pdf"


def _validated_paper_registration_upload_id(upload_id: object) -> str:
    value = str(upload_id or "").strip()
    if not re.fullmatch(r"paper-upload-[0-9a-f]{12}", value):
        raise ValueError("invalid paper registration upload id")
    return value


def _paper_registration_draft_dir(upload_id: object) -> Path:
    value = _validated_paper_registration_upload_id(upload_id)
    return PAPER_STORE.root / "_incoming" / "registration-drafts" / value


def _paper_registration_draft_pdf(upload_id: object) -> Path:
    return _paper_registration_draft_dir(upload_id) / "paper.pdf"


def _paper_registration_draft_state_path(upload_id: object) -> Path:
    return _paper_registration_draft_dir(upload_id) / "draft.json"


def _public_paper_registration_draft(draft: dict) -> dict:
    return {
        key: draft.get(key)
        for key in (
            "upload_id", "filename", "status", "step", "created_at", "updated_at",
            "metadata", "warnings", "error", "usage",
        )
        if draft.get(key) not in (None, "")
    }


def _paper_registration_draft(upload_id: object, *, actor: str) -> dict:
    path = _paper_registration_draft_state_path(upload_id)
    draft = _load_json(path, {})
    if not draft:
        raise KeyError("paper registration upload not found")
    if not is_user_or_resident(actor):
        raise PermissionError("paper registration upload needs a User or Resident")
    return draft


def _set_paper_registration_draft(upload_id: object, patch: dict) -> dict:
    path = _paper_registration_draft_state_path(upload_id)
    with LOCK:
        current = _load_json(path, {})
        if not current:
            raise KeyError("paper registration upload not found")
        updated = {**current, **patch, "updated_at": _now()}
        _write_json(path, updated)
    return updated


def _prune_paper_registration_drafts() -> None:
    root = PAPER_STORE.root / "_incoming" / "registration-drafts"
    if not root.is_dir():
        return
    cutoff = _now() - PAPER_REGISTRATION_DRAFT_TTL_SECONDS
    for path in root.iterdir():
        if not path.is_dir():
            continue
        draft = _load_json(path / "draft.json", {})
        updated_at = int(draft.get("updated_at") or draft.get("created_at") or 0)
        if not updated_at:
            try:
                updated_at = int(path.stat().st_mtime)
            except OSError:
                continue
        if updated_at < cutoff:
            shutil.rmtree(path, ignore_errors=True)


def create_paper_registration_draft(payload: dict, actor: str) -> dict:
    from management_portal import business_tasks

    pdf, filename = _validated_pdf_upload(payload)
    _prune_paper_registration_drafts()
    upload_id = f"paper-upload-{secrets.token_hex(6)}"
    draft_dir = _paper_registration_draft_dir(upload_id)
    draft_dir.mkdir(parents=True, exist_ok=False)
    _paper_registration_draft_pdf(upload_id).write_bytes(pdf)
    draft = {
        "schema": "tfi.paper_registration_draft.v1",
        "upload_id": upload_id,
        "filename": filename,
        "actor": str(actor or ""),
        "status": "queued",
        "step": "queued",
        "created_at": _now(),
        "updated_at": _now(),
        "metadata": {},
        "warnings": [],
        "error": "",
        "usage": {},
    }
    _write_json(_paper_registration_draft_state_path(upload_id), draft)

    def fail(exc: BaseException) -> None:
        try:
            _set_paper_registration_draft(upload_id, {
                "status": "error",
                "step": "error",
                "error": str(exc),
            })
        except Exception:
            pass

    try:
        business_tasks.start(
            f"paper-registration-draft:{upload_id}",
            _run_paper_registration_draft,
            upload_id,
            on_error=fail,
        )
    except Exception as exc:
        fail(exc)
    return _public_paper_registration_draft(
        _paper_registration_draft(upload_id, actor=actor)
    )


def get_paper_registration_draft(upload_id: str, actor: str) -> dict:
    return _public_paper_registration_draft(
        _paper_registration_draft(upload_id, actor=actor)
    )


def _poppler_command(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise RuntimeError(
            f"{name} is not installed; install Poppler for this platform and add its bin directory to PATH"
        )
    return executable


def _pdf_document_properties(pdf_path: Path) -> dict[str, str]:
    try:
        proc = process_runtime.run_process(
            [_poppler_command("pdfinfo"), "-enc", "UTF-8", str(pdf_path)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=90,
        )
    except Exception as exc:
        logging.getLogger(__name__).warning("pdfinfo metadata unavailable: %s", exc)
        return {}
    if proc.returncode != 0:
        return {}
    result: dict[str, str] = {}
    for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() and value.strip():
            result[key.strip().casefold()] = value.strip()
    return result


def _deterministic_paper_registration_metadata(
    pdf_path: Path,
    *,
    filename: str,
    pdf_text: str,
) -> dict:
    properties = _pdf_document_properties(pdf_path)
    title = str(properties.get("title") or "").strip()
    filename_stem = Path(filename).stem.strip()
    property_title_stem = Path(title).stem.strip() if title.casefold().endswith(".pdf") else title
    filename_arxiv_match = re.search(
        r"(?<!\d)((?:\d{4}\.\d{4,5}|[a-z-]+/\d{7})(?:v\d+)?)(?!\d)",
        filename_stem,
        re.I,
    )
    filename_arxiv = filename_arxiv_match.group(1) if filename_arxiv_match else ""
    if (
        title.casefold() in {"untitled", "paper", "document", "microsoft word"}
        or re.match(r"^arxiv\s*:", title, re.I)
        or property_title_stem.casefold() == filename_stem.casefold()
        or bool(filename_arxiv and title.casefold() == filename_arxiv.casefold())
    ):
        title = ""
    authors = _plausible_author_list(properties.get("author"))
    front_matter = _paper_front_matter_metadata(pdf_text)
    title = title or str(front_matter.get("title") or "")
    authors = authors or str(front_matter.get("authors") or "")
    date_text = " ".join(
        str(properties.get(key) or "") for key in ("creationdate", "moddate")
    )
    year = _safe_year(date_text)
    if not year:
        year_match = re.search(r"\b(?:19|20)\d{2}\b", str(pdf_text or "")[:12_000])
        year = year_match.group(0) if year_match else ""
    abstract_match = re.search(
        r"(?ims)^\s*abstract\s*[-—:]?\s*(.{80,5000}?)"
        r"(?=^\s*(?:keywords?|index\s+terms?|(?:1|i)[.\s]+introduction)\b)",
        str(pdf_text or "")[:40_000],
    )
    abstract = re.sub(r"\s+", " ", abstract_match.group(1)).strip() if abstract_match else ""
    identifier_text = "\n".join((str(pdf_text or "")[:100_000], str(properties)))
    doi_match = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", identifier_text, re.I)
    doi = doi_match.group(0).rstrip(".,;:)") if doi_match else ""
    arxiv_match = re.search(
        r"(?:arxiv\s*[:/]\s*)?\b((?:\d{4}\.\d{4,5}|[a-z-]+/\d{7})(?:v\d+)?)\b",
        identifier_text,
        re.I,
    )
    arxiv = (
        arxiv_match.group(1)
        if arxiv_match and "arxiv" in arxiv_match.group(0).casefold()
        else ""
    ) or filename_arxiv
    if not title:
        stem = re.sub(r"[_-]+", " ", filename_stem).strip()
        if (
            len(stem) >= 12
            and stem.casefold() not in {"main", "paper", "document"}
            and not filename_arxiv
        ):
            title = stem
    return {
        "title": title[:320],
        "authors": authors[:600],
        "venue": "",
        "year": year,
        "session": "",
        "abstract": abstract[:5000],
        "tags": [],
        "workload": [],
        "platform": [],
        "architecture": [],
        "optimization": [],
        "doi": doi,
        "arxiv": arxiv,
    }


_AUTHOR_NAME_PARTICLES = frozenset({
    "de", "da", "di", "del", "della", "der", "den", "van", "von", "la", "le", "du", "bin", "binti",
    "al", "el", "dos", "das", "ter", "ten", "y", "zu",
})


def _looks_like_person_name(value: object) -> bool:
    """A person's name as it appears in a byline, not a fragment of prose.

    A synthetic paper without a byline had body sentences split on double
    spaces registered as its authors ("extracted text, structured experimental
    evidence, ..."): every fragment had 2-6 words, which was the only check.
    """
    text = re.sub(r"\s+", " ", str(value or "")).strip(" ,;")
    text = re.sub(r"[\d*†‡§¶✉]+$", "", text).strip()
    if not text or len(text) > 80:
        return False
    if re.fullmatch(r"[\u4e00-\u9fff·•]{2,8}", text):
        return True
    # Experimental labels can look title-cased without being a byline.
    if re.fullmatch(r"(?:method|model|baseline|variant|group)\s+[A-Z]", text, re.I):
        return False
    words = text.split(" ")
    if not 2 <= len(words) <= 6:
        return False
    for word in words:
        bare = re.sub(r"[\d*†‡§¶✉,;]+$", "", word)
        if not bare:
            return False
        if bare.casefold() in _AUTHOR_NAME_PARTICLES:
            continue
        if re.fullmatch(r"[A-Z]\.(?:-?[A-Z]\.)*", bare):
            continue
        if not (bare[0].isalpha() and bare[0].isupper()) or re.search(r"[.!?:]$", bare):
            return False
    return True


def _plausible_author_list(value: object) -> str:
    """The author list when it reads as person names, otherwise empty."""
    if isinstance(value, list):
        value = ", ".join(str(item or "").strip() for item in value if str(item or "").strip())
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    parts = [part for part in re.split(r"\s*(?:,|;|，|、|\band\b)\s*", text) if part.strip()]
    names = [re.sub(r"[\d*†‡§¶✉]+$", "", part.strip()).strip() for part in parts if _looks_like_person_name(part)]
    # A byline may carry "et al." or "Jr."; prose fails most of its pieces.
    return ", ".join(names) if names and len(names) >= 0.8 * len(parts) else ""


def _paper_front_matter_metadata(pdf_text: str) -> dict[str, str]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for raw_line in str(pdf_text or "")[:16_000].splitlines()[:120]:
        stripped = raw_line.strip()
        if re.fullmatch(r"abstract", stripped, re.I):
            break
        if not stripped:
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(raw_line.rstrip())
    if current:
        blocks.append(current)

    def ignored(line: str) -> bool:
        text = line.strip()
        return bool(
            not text
            or re.search(r"\barxiv\s*:", text, re.I)
            or re.search(r"\bdoi\s*:", text, re.I)
            or "@" in text
            or re.fullmatch(r"(?:19|20)\d{2}", text)
            or re.match(r"^(?:proceedings|copyright|preprint|submitted|accepted)\b", text, re.I)
        )

    title = ""
    title_block_index = -1
    for index, block in enumerate(blocks[:6]):
        lines = [re.sub(r"\s+", " ", line).strip() for line in block if not ignored(line)]
        candidate = " ".join(lines).strip()
        if 12 <= len(candidate) <= 320 and len(re.findall(r"[A-Za-z]", candidate)) >= 8:
            title = candidate
            title_block_index = index
            break

    authors: list[str] = []
    for block in blocks[title_block_index + 1:title_block_index + 4] if title_block_index >= 0 else []:
        for line in block:
            if ignored(line):
                continue
            for candidate in re.split(r"\s{2,}|\s+and\s+", line.strip(), flags=re.I):
                name = re.sub(r"^(?:and|&)\s+", "", re.sub(r"\s+", " ", candidate).strip(" ,;"), flags=re.I)
                words = name.split()
                if (
                    2 <= len(words) <= 6
                    and len(name) <= 100
                    and not re.search(r"\b(?:university|institute|laboratory|department|google|microsoft|meta|nvidia)\b", name, re.I)
                    and all(re.search(r"[A-Za-z]", word) for word in words)
                    and _looks_like_person_name(name)
                ):
                    authors.append(name)
        if authors:
            break
    return {
        "title": title,
        "authors": ", ".join(authors[:30]),
    }


def _crossref_registration_metadata(doi: str) -> dict:
    clean_doi = str(doi or "").strip().removeprefix("https://doi.org/")
    if not clean_doi:
        return {}
    request = urllib.request.Request(
        "https://api.crossref.org/works/" + quote(clean_doi, safe=""),
        headers={"User-Agent": "TFIPortal/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = json.loads(response.read(2_000_000))
    except Exception:
        return {}
    message = payload.get("message") if isinstance(payload, dict) else {}
    if not isinstance(message, dict):
        return {}
    titles = message.get("title") if isinstance(message.get("title"), list) else []
    venues = message.get("container-title") if isinstance(message.get("container-title"), list) else []
    authors = []
    for author in message.get("author") if isinstance(message.get("author"), list) else []:
        if not isinstance(author, dict):
            continue
        name = " ".join(
            part for part in (
                str(author.get("given") or "").strip(),
                str(author.get("family") or "").strip(),
            )
            if part
        )
        if name:
            authors.append(name)
    year = ""
    for date_key in ("published-print", "published-online", "issued", "created"):
        date_value = message.get(date_key) if isinstance(message.get(date_key), dict) else {}
        date_parts = date_value.get("date-parts") if isinstance(date_value.get("date-parts"), list) else []
        if date_parts and isinstance(date_parts[0], list) and date_parts[0]:
            year = _safe_year(date_parts[0][0])
            if year:
                break
    abstract = html_lib.unescape(
        re.sub(r"<[^>]+>", " ", str(message.get("abstract") or ""))
    )
    return {
        "title": str(titles[0] if titles else "").strip(),
        "authors": ", ".join(authors),
        "venue": str(venues[0] if venues else "").strip(),
        "year": year,
        "abstract": re.sub(r"\s+", " ", abstract).strip(),
        "doi": clean_doi,
    }


def _identifier_paper_registration_metadata(metadata: dict) -> dict:
    arxiv = str(metadata.get("arxiv") or "").strip()
    if arxiv:
        fetched = _fetch_arxiv_metadata(id_list=re.sub(r"v\d+$", "", arxiv, flags=re.I))
        if fetched:
            return {
                "title": fetched.get("title") or "",
                "authors": fetched.get("authors") or "",
                "venue": "arXiv",
                "year": fetched.get("year") or "",
                "abstract": fetched.get("abstract") or "",
                # Keep the exact uploaded identifier (including a version suffix)
                # while using the versionless id for the metadata lookup above.
                "arxiv": arxiv,
            }
    doi = str(metadata.get("doi") or "").strip()
    return _crossref_registration_metadata(doi) if doi else {}


def _coerce_paper_registration_metadata(value: dict) -> dict:
    authors = _plausible_author_list(value.get("authors"))
    return {
        "title": str(value.get("title") or "").strip()[:320],
        "authors": str(authors or "").strip()[:600],
        "venue": str(value.get("venue") or "").strip()[:80],
        "year": _safe_year(value.get("year")),
        "session": str(value.get("session") or "").strip()[:120],
        "abstract": str(value.get("abstract") or "").strip()[:5000],
        "tags": _normalize_list(value.get("tags"))[:12],
        "workload": _normalize_list(value.get("workload"))[:8],
        "platform": _normalize_list(value.get("platform"))[:8],
        "architecture": _normalize_list(value.get("architecture"))[:8],
        "optimization": _normalize_list(value.get("optimization"))[:8],
        "doi": str(value.get("doi") or "").strip()[:200],
        "arxiv": str(value.get("arxiv") or "").strip()[:80],
    }


def _paper_registration_project_catalog() -> list[dict[str, str]]:
    try:
        from management_portal import project_registry

        projects = project_registry.projects()
    except Exception:
        return []
    result: list[dict[str, str]] = []
    for item in projects or []:
        if not isinstance(item, dict):
            continue
        project_id = str(item.get("id") or "").strip()
        if not _is_project_relation_id(project_id):
            continue
        result.append({
            "id": project_id,
            "description": str(item.get("description") or "").strip(),
            "status": str(item.get("status") or "").strip(),
        })
    return result


def _heuristic_paper_registration_project(
    source: str,
    project_catalog: list[dict[str, str]],
) -> str:
    available = {str(item.get("id") or "").strip(): item for item in project_catalog}
    hints = (
        ("GPUDataflowRuntime", ("megakernel", "persistent kernel", "split-join dataflow", "gpu dataflow runtime")),
        ("HardwareExplorer", ("hardware exploration", "architecture exploration", "accelerator design", "manycore architecture")),
        ("ACLink-Co-Kernel", ("co-kernel", "distributed os kernel", "event-triggered kernel")),
        ("ACLink", ("network-on-chip", "network-on-package", "event-triggered interconnect", "distributed scheduling protocol")),
        ("SemanticPaging", ("semantic paging", "semantic virtual memory", "kv cache management")),
        ("SparseTriggerArch", ("sparse trigger", "event-triggered architecture", "fine-grained sparse")),
        ("FlowTango", ("streaming language", "streaming runtime", "dataflow language")),
        ("DRAMStacking", ("dram stacking", "stacked dram", "3d-stacked memory")),
        ("CATFlow", ("distributed training", "parallel plan", "strategy discovery")),
        ("OSForAgent", ("embodied agent", "agent harness", "agentic os", "operating system for agent", "runtime critic", "physical intelligence")),
        ("RobotWorker", ("robot manipulation", "robot navigation", "vision-language-action", "vla policy", "learning-based robot control")),
        ("RoboLab", ("cloud robot laboratory", "remote robot experiment", "robot lab automation")),
        ("Simulator_Asset_Library", ("simulator asset", "simulation asset", "scene generation", "sim-to-real")),
        ("ScienceRoboticsViewpoints", ("science robotics viewpoint", "robotics perspective", "future of robotics")),
    )
    scored: list[tuple[int, str]] = []
    for project_id, needles in hints:
        if project_id not in available:
            continue
        score = sum(4 for needle in needles if needle in source)
        if score:
            scored.append((score, project_id))
    if scored:
        return max(scored, key=lambda item: item[0])[1]

    stopwords = {
        "about", "agent", "based", "company", "from", "paper", "project",
        "research", "system", "that", "this", "with",
    }
    source_tokens = {
        token for token in re.findall(r"[a-z][a-z0-9-]{3,}", source)
        if token not in stopwords
    }
    lexical_scores: list[tuple[int, str]] = []
    for project_id, item in available.items():
        description = " ".join((project_id, item.get("description") or "")).casefold()
        description_tokens = {
            token for token in re.findall(r"[a-z][a-z0-9-]{3,}", description)
            if token not in stopwords
        }
        overlap = len(source_tokens & description_tokens)
        if overlap >= 2:
            lexical_scores.append((overlap, project_id))
    return max(lexical_scores, default=(0, ""), key=lambda item: item[0])[1]


def _heuristic_paper_registration_classification(
    metadata: dict,
    pdf_text: str,
    *,
    project_catalog: list[dict[str, str]] | None = None,
) -> dict:
    """Derive conservative search facets when the model pool is unavailable."""
    source = " ".join((
        str(metadata.get("title") or ""),
        str(metadata.get("abstract") or ""),
        str(pdf_text or "")[:30_000],
    )).casefold()

    def matches(rules: tuple[tuple[tuple[str, ...], str], ...], limit: int) -> list[str]:
        values: list[str] = []
        for needles, label in rules:
            if any(needle in source for needle in needles) and label not in values:
                values.append(label)
            if len(values) >= limit:
                break
        return values

    catalog = project_catalog if project_catalog is not None else _paper_registration_project_catalog()
    return {
        "session": _heuristic_paper_registration_project(source, catalog),
        "tags": matches((
            (("embodied intelligence", "embodied agent"), "embodied intelligence"),
            (("robot", "robotic"), "robotics"),
            (("self-evolving", "self evolution", "self-improvement"), "self-evolving agents"),
            (("closed-loop", "closed loop"), "closed-loop control"),
            (("world model",), "world models"),
            (("reinforcement learning",), "reinforcement learning"),
            (("imitation learning",), "imitation learning"),
            (("large language model", " llm "), "large language models"),
            (("computer vision", "visual recognition"), "computer vision"),
            (("natural language processing",), "natural language processing"),
            (("memory system", "memory hierarchy"), "memory systems"),
            (("accelerator", "processing-in-memory", "compute-in-memory"), "computer architecture"),
        ), 8),
        "workload": matches((
            (("robot manipulation", "manipulation task"), "robot manipulation"),
            (("robot navigation", "navigation task"), "robot navigation"),
            (("robot", "robotic", "embodied agent"), "robot control"),
            (("policy inference", "inference speedup"), "policy inference"),
            (("model training", "training workload"), "model training"),
            (("machine translation",), "machine translation"),
            (("image generation",), "image generation"),
            (("graph processing", "graph neural network"), "graph processing"),
        ), 6),
        "platform": matches((
            (("physical robot", "robot-environment", "robotic"), "robot"),
            ((" gpu", "gpu ", "cuda"), "GPU"),
            ((" cpu", "cpu "), "CPU"),
            (("fpga",), "FPGA"),
            (("edge device", "edge platform"), "edge device"),
            (("cloud platform", "cloud cluster"), "cloud"),
            (("ros ", "robot operating system"), "ROS"),
        ), 6),
        "architecture": matches((
            (("closed-loop", "closed loop"), "closed-loop agent"),
            (("agentic harness", "embodied harness", "agent harness"), "agent harness"),
            (("world model",), "world model"),
            (("actor-critic", "actor critic"), "actor-critic"),
            (("transformer",), "Transformer"),
            (("diffusion model", "diffusion policy"), "diffusion model"),
            (("mixture of experts", "mixture-of-experts"), "mixture-of-experts"),
            (("encoder-decoder", "encoder decoder"), "encoder-decoder"),
            (("graph neural network",), "graph neural network"),
            (("systolic array",), "systolic array"),
            (("chiplet",), "chiplet"),
        ), 6),
        "optimization": matches((
            (("self-evolving", "self evolution", "self-improvement"), "online self-improvement"),
            (("online adaptation", "online while", "online update"), "online adaptation"),
            (("recovery skill", "runtime recovery"), "runtime recovery"),
            (("frozen base policy", "policy frozen"), "frozen-policy adaptation"),
            (("quantization",), "quantization"),
            (("knowledge distillation", "distillation"), "knowledge distillation"),
            (("pruning",), "pruning"),
            (("parallelization", "parallel execution"), "parallelization"),
            (("scheduling",), "scheduling"),
            (("caching", "cache reuse"), "caching"),
        ), 6),
    }


def _ai_paper_registration_metadata(pdf_text: str, *, actor: str = "") -> tuple[dict, dict]:
    system_prompt = (
        "你是 TFI Portal 的论文元数据提取器。只根据给定 PDF 文本提取信息；"
        "书目信息缺失时使用空字符串，不得猜测；分类字段应根据论文语义归纳，"
        "只有确实不适用时才使用空数组。只返回 JSON 对象。"
    )
    project_catalog = _paper_registration_project_catalog()
    project_options = "\n".join(
        f"- {item['id']}: {item.get('description') or ''}"
        for item in project_catalog
    ) or "- (no project options available)"
    user_prompt = f"""请从下面的论文 PDF 文本中提取注册信息。

JSON schema:
{{
  "title": "论文完整标题",
  "authors": ["作者姓名"],
  "venue": "正式会议、期刊名称或 arXiv；无法确定则为空",
  "year": "四位年份",
  "session": "最相关的现有项目 ID；无法可靠判断则为空",
  "abstract": "论文原始摘要，保持原语言",
  "tags": ["最多 8 个检索主题"],
  "workload": ["论文处理的工作负载"],
  "platform": ["硬件或软件平台"],
  "architecture": ["关键架构"],
  "optimization": ["关键优化技术"],
  "doi": "DOI，不含 https://doi.org/",
  "arxiv": "arXiv ID"
}}

规则：
- title、authors、venue、year、abstract 必须来自正文可见内容。
- authors 只提取明确署名的作者；没有署名时返回空数组，不把方法、模型、实验组或表格标签当作人名。
- tags 和分类字段使用简短英文术语，避免宽泛词和重复词。
- session 只能从下面的现有项目中选择一个，必须原样返回项目 ID，禁止创造项目：
{project_options}
- 不要输出说明、Markdown 或代码围栏。

PDF text:
{str(pdf_text or '')[:60_000]}
"""
    content, usage = _resident_reply(
        f"{system_prompt}\n\n{user_prompt}",
        actor=actor,
        resident_scope="paper:registration-metadata",
        cwd=str(WORKSPACE_ROOT),
        metadata={"business_type": "paper_registration_metadata"},
    )
    extracted = _coerce_paper_registration_metadata(_parse_json_object_from_text(content))
    if extracted.get("session") not in {item["id"] for item in project_catalog}:
        extracted["session"] = ""
    return extracted, usage


def _run_paper_registration_draft(upload_id: str) -> dict:
    draft = _load_json(_paper_registration_draft_state_path(upload_id), {})
    if not draft:
        raise KeyError("paper registration upload not found")
    pdf_path = _paper_registration_draft_pdf(upload_id)
    warnings: list[str] = []
    _set_paper_registration_draft(upload_id, {
        "status": "running",
        "step": "extracting PDF text",
        "error": "",
    })
    text_path = _paper_registration_draft_dir(upload_id) / "pdf.txt"
    try:
        pdf_text = _extract_pdf_text_file(pdf_path, cache_path=text_path)
    except Exception as exc:
        pdf_text = ""
        warnings.append(f"PDF text extraction failed: {exc}")
    metadata = _deterministic_paper_registration_metadata(
        pdf_path,
        filename=str(draft.get("filename") or "paper.pdf"),
        pdf_text=pdf_text,
    )
    identifier_metadata = _identifier_paper_registration_metadata(metadata)
    for key, value in identifier_metadata.items():
        if value not in (None, "", []):
            metadata[key] = value
    usage = {}
    _set_paper_registration_draft(upload_id, {
        "status": "running",
        "step": "extracting metadata",
        "metadata": metadata,
    })
    core_metadata_ready = all(
        str(metadata.get(key) or "").strip()
        for key in ("title", "authors", "year", "abstract")
    )
    if not pdf_text.strip():
        warnings.append("PDF contains no extractable text")
    else:
        heuristic_metadata = _heuristic_paper_registration_classification(metadata, pdf_text)
        for key, value in heuristic_metadata.items():
            if value:
                metadata[key] = value
        _set_paper_registration_draft(upload_id, {
            "status": "running",
            "step": "refining semantic classification",
            "metadata": metadata,
        })
        try:
            # Identifier services provide reliable bibliography, but not Portal's
            # semantic classification fields. Keep the trusted fields and still
            # run one bounded enrichment pass over the abstract/body excerpt.
            ai_source_text = pdf_text
            if identifier_metadata and core_metadata_ready:
                ai_source_text = (
                    "Known authoritative metadata:\n"
                    f"Title: {metadata.get('title') or ''}\n"
                    f"Authors: {metadata.get('authors') or ''}\n"
                    f"Venue: {metadata.get('venue') or ''}\n"
                    f"Year: {metadata.get('year') or ''}\n"
                    f"Abstract: {metadata.get('abstract') or ''}\n\n"
                    f"PDF body excerpt:\n{pdf_text[:20_000]}"
                )
            ai_metadata, usage = _ai_paper_registration_metadata(
                ai_source_text,
                actor=str(draft.get("actor") or ""),
            )
            for key, value in ai_metadata.items():
                if value in (None, "", []):
                    continue
                if key in {"tags", "workload", "platform", "architecture", "optimization"}:
                    metadata[key] = value
                elif identifier_metadata.get(key) in (None, "", []):
                    metadata[key] = value
        except Exception as exc:
            warnings.append(f"AI metadata extraction failed: {exc}")
    status = "done" if metadata.get("title") and (metadata.get("authors") or metadata.get("abstract")) else "partial"
    result = _set_paper_registration_draft(upload_id, {
        "status": status,
        "step": "ready",
        "metadata": metadata,
        "warnings": warnings,
        "usage": usage,
        "error": "",
    })
    return _public_paper_registration_draft(result)


def delete_paper(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    if not paper_id:
        raise ValueError("paper_id required")
    with LOCK:
        paper = _paper_by_id(paper_id)
        if not paper:
            raise KeyError("paper not found")
        from .workbench_projections.resource_lifecycle import release_resource
        release_resource(paper_id, actor=actor)
        for review in _all_reviews():
            if str(review.get('paper_id') or '') == paper_id:
                release_resource(review['review_id'], actor=actor)
        store_deleted = PAPER_STORE.delete_paper(paper_id)
        kinds = ("review_jobs", "reviews", "notes", "deep_read_jobs", "exp_jobs")
        with paper_state_store.locked(*kinds):
            for kind in kinds:
                paper_state_store.delete_paper(kind, paper_id)
    # The package's review files went with it.
    _announce_review_changes()
    return {
        "deleted": True,
        "paper_id": paper_id,
        "title": str(paper.get("title") or ""),
        "deleted_by": actor,
        "deleted_at": _now(),
        "store": store_deleted or {},
    }


def upload_paper_pdf(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    if not paper_id:
        raise ValueError("paper_id required")
    data, _filename = _validated_pdf_upload(payload)
    with LOCK:
        found = PAPER_STORE.manifest_for_paper_id(paper_id)
        if not found:
            raise KeyError("paper store manifest not found")
        manifest_path, manifest = found
        paper_dir = PAPER_STORE.paper_dir_from_manifest(manifest, manifest_path)
        try:
            resolved_dir = paper_dir.resolve(strict=False)
            resolved_items = PAPER_STORE.items_root.resolve(strict=False)
        except Exception:
            raise ValueError("paper store path unavailable")
        if resolved_items not in resolved_dir.parents:
            raise ValueError(f"refusing to write PDF outside Paper items: {paper_dir}")
        paper_dir.mkdir(parents=True, exist_ok=True)
        target = paper_dir / "paper.pdf"
        target.write_bytes(data)
        fields = {"pdf_path": str(target)}
        if str(manifest.get("status") or "").strip() == "pdf-missing":
            fields["status"] = "registered"
        updated = _update_paper_fields(paper_id, fields)
        for rel in ("text/pdf.raw.txt", "text/pdf.clean.txt"):
            path = paper_dir / rel
            if path.exists():
                path.unlink()
    updated_id = str(updated.get("id") or paper_id)
    _start_assets_task(updated_id, actor=actor)
    return get_paper_detail(updated_id)


def delete_paper_pdf(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    if not paper_id:
        raise ValueError("paper_id required")
    with LOCK:
        found = PAPER_STORE.manifest_for_paper_id(paper_id)
        if not found:
            raise KeyError("paper store manifest not found")
        manifest_path, manifest = found
        paper_dir = PAPER_STORE.paper_dir_from_manifest(manifest, manifest_path)
        try:
            resolved_dir = paper_dir.resolve(strict=False)
            resolved_items = PAPER_STORE.items_root.resolve(strict=False)
        except Exception:
            raise ValueError("paper store path unavailable")
        if resolved_items not in resolved_dir.parents:
            raise ValueError(f"refusing to delete PDF outside Paper items: {paper_dir}")

        deleted_paths = []
        for rel in ("paper.pdf", "text/pdf.raw.txt", "text/pdf.clean.txt"):
            path = paper_dir / rel
            if path.exists():
                path.unlink()
                deleted_paths.append(str(path))
        updated = _update_paper_fields(paper_id, {"pdf_path": "", "status": "pdf-missing"})
    detail = get_paper_detail(str(updated.get("id") or paper_id))
    detail["pdf_delete"] = {
        "deleted": True,
        "deleted_paths": deleted_paths,
        "deleted_by": actor,
        "deleted_at": _now(),
    }
    return detail


def upload_figure_input_image(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    data = payload.get("data_bytes")
    filename = str(payload.get("filename") or "image.png").strip()
    if not paper_id:
        raise ValueError("paper_id required")
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("image file required")
    if len(data) > PAPER_FIGURE_IMAGE_MAX_BYTES:
        raise ValueError("image file is too large")
    paper = _paper_by_id(paper_id)
    if not paper:
        raise KeyError("paper not found")
    paper_root = _paper_root(paper)
    if not paper_root:
        raise ValueError("paper store root unavailable")
    try:
        image = Image.open(BytesIO(bytes(data)))
        width, height = image.size
        if (
            width <= 0
            or height <= 0
            or width * height > PAPER_FIGURE_IMAGE_MAX_PIXELS
        ):
            raise ValueError("image dimensions are too large")
        image.load()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("uploaded file is not a readable image") from exc
    target_dir = paper_root / "figures" / "input_images"
    target_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(8)
    target = target_dir / f"{token}.png"
    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGBA")
    image.save(target, format="PNG")
    rel_path = target.relative_to(paper_root).as_posix()
    return {
        "paper_id": paper_id,
        "filename": filename,
        "path": str(target),
        "rel_path": rel_path,
        "created_by": str(actor or "").strip(),
        "created_at": _now(),
    }


def _recover_paper_collection_job(job: dict, actor: str) -> dict:
    from management_portal import business_tasks
    from management_portal.paper_collector import require_actor
    from management_portal.paper_projection import input_receipt

    if (not actor or job.get("created_by") != actor or job.get("deleted")
            or job.get("status") not in {"queued", "running"}
            or not schema_matches(job.get("collector_contract"), "tfi.paper.collector.v2")):
        return job
    try:
        require_actor(actor)
    except PermissionError:
        return job
    job_id = str(job.get("job_id") or "")
    with LOCK, paper_state_store.locked("collector_jobs"):
        current = paper_state_store.get("collector_jobs", job_id)
        if current is None:
            return job
        if (current.get("deleted") or current.get("status") not in {"queued", "running"}
                or current.get("created_by") != actor
                or not schema_matches(current.get("collector_contract"), "tfi.paper.collector.v2")
                or "existing_paper_ids" not in current
                or business_tasks.is_active(f"paper-collection:{job_id}")):
            return current
        try:
            binding = input_receipt(current.get("invocation") or {})
        except ValueError:
            return current
        if (current["invocation"].get("input_identity_status") == "unconfirmed"
                or not all(binding.get(key) for key in ("resident_id", "lineage_id", "input_message_id"))):
            return current
        owner = current.get("process_owner") or {}
        here = business_tasks.process_identity()
        if owner and owner != here and business_tasks.process_is_alive(owner):
            return current
        # Claim observation under the durable store lock. Never manufacture the
        # original caller's credentials or replay a missing/unconfirmed input.
        current = paper_state_store.merge("collector_jobs", job_id, {"process_owner": here})
    return _start_paper_collection_task(current)


def _is_stale_paper_collection_job(job: dict) -> bool:
    from management_portal import business_tasks

    if job.get("deleted") or str(job.get("status") or "") not in {"queued", "running"}:
        return False
    if business_tasks.is_active(f"paper-collection:{job.get('job_id') or ''}"):
        return False
    if job.get("process_owner") and business_tasks.process_is_alive(job["process_owner"]):
        return False
    touched = int(job.get("updated_at") or job.get("created_at") or 0)
    return touched > 0 and _now() - touched > COLLECTOR_STALE_SECONDS


def list_paper_collection_jobs(actor: str = "") -> dict:
    actor = str(actor or "").strip()
    with LOCK:
        jobs = list(_all_paper_collection_jobs())
    jobs = [job for job in jobs if not job.get("deleted")]
    jobs = [_recover_paper_collection_job(job, actor) for job in jobs]
    jobs = [
        _set_paper_collection_job(str(job.get("job_id") or ""), {
            "status": "error", "step": "stale", "progress": 100,
            "error": "Collector 任务已中断（执行进程重启或长时间没有更新）；请重新收集。",
        }) if _is_stale_paper_collection_job(job) else job
        for job in jobs
    ]
    jobs = [job for job in jobs if not job.get("deleted")]
    jobs = sorted(jobs, key=lambda item: int(item.get("updated_at") or item.get("created_at") or 0), reverse=True)
    return {"jobs": [_public_paper_collection_job(job) for job in jobs[:30]]}


def start_paper_collection(payload: dict, actor: str = "") -> dict:
    from management_portal import business_tasks
    from management_portal.paper_collector import CONTRACT, MAX_DIRECTION_CHARS, require_actor

    actor = require_actor(actor)
    direction = str(payload.get("direction") or payload.get("query") or "").strip()
    if not direction:
        raise ValueError("collection direction required")
    if len(direction) > MAX_DIRECTION_CHARS:
        raise ValueError(f"collection direction exceeds {MAX_DIRECTION_CHARS} characters; no input was truncated")
    try:
        max_papers = int(payload.get("max_papers") or 50)
    except Exception:
        max_papers = 50
    max_papers = max(1, min(50, max_papers))
    job_id = f"collector-{secrets.token_hex(6)}"
    job = {
        "job_id": job_id,
        "direction": direction,
        "collector_contract": CONTRACT,
        "requested_message_id": f"msg_{secrets.token_hex(16)}",
        "process_owner": business_tasks.process_identity(),
        "max_papers": max_papers,
        "status": "queued",
        "step": "queued",
        "progress": 0,
        "created_by": str(actor or "").strip(),
        "created_at": _now(),
        "updated_at": _now(),
        "registered_count": 0,
        "error": "",
        "summary": "",
    }
    with LOCK:
        paper_state_store.append("collector_jobs", job)
    return _public_paper_collection_job(_start_paper_collection_task(job))


def delete_paper_collection_job(payload: dict, actor: str = "") -> dict:
    job_id = str(payload.get("job_id") or "").strip()
    if not job_id:
        raise ValueError("paper collection job id required")
    with LOCK:
        job = _paper_collection_job_by_id(job_id)
        if not job or job.get("deleted"):
            raise KeyError("paper collection job not found")
        _set_paper_collection_job(job_id, {"deleted": True, "status": "deleted", "step": "deleted", "progress": 100})
    return {"ok": True, "job_id": job_id}


def resolve_asset(paper_id: str, kind: str, asset_id: str = "", generated_id: str = "") -> tuple[Path, str]:
    paper = _paper_for_asset_lookup(paper_id)
    if not paper:
        raise KeyError("paper not found")
    if kind == "pdf":
        path = _paper_store_file(paper, "pdf") or _safe_file_path(str(paper.get("pdf_path") or ""))
        content_type = "application/pdf"
    elif kind == "analysis":
        path = _paper_store_file(paper, "analysis") or _safe_file_path(str(paper.get("analysis_path") or ""))
        content_type = "text/markdown; charset=utf-8"
    elif kind in {"figure", "generated_figure"}:
        source = next((item for item in _load_figures(paper) if str(item.get("figure_id") or "") == str(asset_id or "")), None)
        rel_path = ""
        if source and kind == "figure":
            rel_path = str(source.get("image_rel_path") or "")
        elif source and kind == "generated_figure":
            generated = next((item for item in source.get("generated") or [] if str(item.get("generated_id") or "") == str(generated_id or "")), None)
            rel_path = str((generated or {}).get("image_rel_path") or "")
        paper_root = _paper_root(paper)
        path = _paper_bundle_path(paper_root, rel_path) if paper_root and rel_path else None
        content_type = _media_content_type_for_path(path)
    else:
        raise ValueError("unknown asset kind")
    if not path or not path.is_file():
        raise FileNotFoundError(kind)
    return path, content_type


def _media_content_type_for_path(path: Path | None) -> str:
    suffix = (path.suffix if path else "").casefold()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "application/octet-stream"


def start_deep_read(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    if not paper_id:
        raise ValueError("paper_id required")
    with LOCK:
        papers = _all_papers()
        paper = next((item for item in papers if str(item.get("id") or "") == paper_id), None)
        if not paper:
            raise KeyError("paper not found")
        public = _paper_public(paper)
        if not public.get("has_pdf"):
            raise ValueError("paper has no local or remote PDF")
        with paper_state_store.locked("deep_read_jobs"):
            latest = _latest_deep_read_job(paper_id=paper_id)
            if latest and str(latest.get("status") or "") in {"queued", "running"}:
                return latest
            job_id = f"deep-read-{secrets.token_hex(6)}"
            job = {
                "job_id": job_id,
                "paper_id": paper_id,
                "paper_title": str(paper.get("title") or ""),
                "status": "queued",
                "step": "queued",
                "progress": 0,
                "created_by": actor,
                "created_at": _now(),
                "updated_at": _now(),
                "analysis_path": "",
                "error": "",
                "usage": {},
            }
            paper_state_store.append("deep_read_jobs", job)
    return _launch_deep_read_business_task(job)


def _launch_deep_read_business_task(job: dict) -> dict:
    job_id = str(job.get("job_id") or "")
    if not job_id:
        raise ValueError("Deep Read job id required")
    with _DEEP_READ_TASK_LOCK:
        if job_id in _ACTIVE_DEEP_READ_TASKS:
            return _latest_deep_read_job(job_id=job_id) or job
        _ACTIVE_DEEP_READ_TASKS.add(job_id)

    def run() -> None:
        try:
            _run_deep_read_job(job_id)
        finally:
            with _DEEP_READ_TASK_LOCK:
                _ACTIVE_DEEP_READ_TASKS.discard(job_id)

    threading.Thread(
        target=run,
        name=f"paper-deep-read-{job_id}",
        daemon=True,
    ).start()
    return _latest_deep_read_job(job_id=job_id) or job


def _start_summary_task(paper_id: str, *, actor: str = "") -> None:
    from management_portal import business_tasks

    paper_id = str(paper_id or "").strip()
    if not paper_id:
        return
    job_id = f"summary-{int(time.time())}-{secrets.token_hex(5)}"
    _write_paper_run_record(paper_id, {
        "job_id": job_id,
        "kind": "summary",
        "status": "queued",
        "actor": str(actor or ""),
        "queued_at": _now(),
        "step": "queued",
        "error": "",
    })
    def fail(exc: BaseException) -> None:
        _update_paper_run_record(paper_id, {
            "job_id": job_id,
            "kind": "summary",
            "status": "error",
            "step": "error",
            "finished_at": _now(),
            "error": str(exc),
        })

    try:
        business_tasks.start(
            f"paper-summary:{job_id}",
            _run_summary_job,
            paper_id,
            actor=actor,
            job_id=job_id,
            on_error=fail,
        )
    except Exception as exc:
        _update_paper_run_record(paper_id, {
            "job_id": job_id,
            "kind": "summary",
            "status": "error",
            "step": "launch error",
            "finished_at": _now(),
            "error": str(exc),
        })
        print(f"[paper-summary] failed to launch for {paper_id}: {exc}", flush=True)


def _start_assets_task(paper_id: str, *, actor: str = "") -> None:
    from management_portal import business_tasks

    paper_id = str(paper_id or "").strip()
    if not paper_id:
        return
    job_id = f"assets-{int(time.time())}-{secrets.token_hex(5)}"
    _write_paper_run_record(paper_id, {
        "job_id": job_id,
        "kind": "assets",
        "status": "queued",
        "actor": str(actor or ""),
        "queued_at": _now(),
        "step": "queued",
        "refs": None,
        "figures": None,
        "errors": [],
    })
    def fail(exc: BaseException) -> None:
        _update_paper_run_record(paper_id, {
            "job_id": job_id,
            "kind": "assets",
            "status": "error",
            "step": "error",
            "finished_at": _now(),
            "error": str(exc),
        })

    try:
        business_tasks.start(
            f"paper-assets:{job_id}",
            _run_assets_job,
            paper_id,
            actor=actor,
            job_id=job_id,
            on_error=fail,
        )
    except Exception as exc:
        _update_paper_run_record(paper_id, {
            "job_id": job_id,
            "kind": "assets",
            "status": "error",
            "step": "launch error",
            "finished_at": _now(),
            "error": f"Failed to run assets business task: {exc}",
        })
        print(f"[paper-assets] failed to launch for {paper_id}: {exc}", flush=True)


def _paper_asset_lock_path() -> Path:
    PAPER_ASSETS_WORK_ROOT.mkdir(parents=True, exist_ok=True)
    return PAPER_ASSETS_WORK_ROOT / "paper-assets.lock"


def _run_assets_job(paper_id: str, *, actor: str = "", job_id: str = "") -> dict:
    paper_id = str(paper_id or "").strip()
    if not paper_id:
        raise ValueError("paper_id required")
    job_id = str(job_id or f"manual-assets-{secrets.token_hex(4)}").strip()
    existing = _paper_run_record_by_job_id(paper_id, job_id)
    if not existing:
        _write_paper_run_record(paper_id, {
            "job_id": job_id,
            "kind": "assets",
            "status": "queued",
            "actor": str(actor or ""),
            "queued_at": _now(),
            "step": "queued",
            "errors": [],
        })
    lock_path = _paper_asset_lock_path()
    try:
        with lock_path.open("a+b") as lock_file, lock_stream(lock_file):
            _update_paper_run_record(paper_id, {
                "job_id": job_id,
                "kind": "assets",
                "status": "running",
                "actor": str(actor or ""),
                "started_at": _now(),
                "step": "extracting refs and figures",
                "error": "",
                "errors": [],
            })
            paper = _paper_by_id(paper_id)
            if not paper:
                raise KeyError(f"paper not found: {paper_id}")
            try:
                pdf_path = _local_pdf_for_paper(paper)
            except FileNotFoundError:
                pdf_path = None
            if not pdf_path or not pdf_path.is_file():
                print(f"[paper-assets] skip {paper_id}: local PDF unavailable", flush=True)
                result = {"paper_id": paper_id, "skipped": True, "reason": "local PDF unavailable"}
                _update_paper_run_record(paper_id, {
                    "job_id": job_id,
                    "kind": "assets",
                    "status": "skipped",
                    "actor": str(actor or ""),
                    "finished_at": _now(),
                    "reason": result["reason"],
                    "refs": None,
                    "figures": None,
                    "errors": [],
                })
                return result
            result: dict[str, Any] = {"paper_id": paper_id, "skipped": False, "refs": None, "figures": None, "errors": []}
            try:
                refs = extract_refs({"paper_id": paper_id}, actor)
                result["refs"] = int(refs.get("count") or 0)
            except Exception as exc:
                message = f"refs failed: {exc}"
                print(f"[paper-assets] {paper_id} {message}", flush=True)
                result["errors"].append(message)
            try:
                figures = extract_figures({"paper_id": paper_id}, actor)
                result["figures"] = int(figures.get("count") or 0)
            except Exception as exc:
                message = f"figures failed: {exc}"
                print(f"[paper-assets] {paper_id} {message}", flush=True)
                result["errors"].append(message)
            final_status = "error" if result.get("errors") else "done"
            _update_paper_run_record(paper_id, {
                "job_id": job_id,
                "kind": "assets",
                "status": final_status,
                "actor": str(actor or ""),
                "finished_at": _now(),
                "step": final_status,
                "refs": result.get("refs"),
                "figures": result.get("figures"),
                "errors": result.get("errors") or [],
            })
            print(
                f"[paper-assets] done {paper_id}: refs={result.get('refs')} figures={result.get('figures')} errors={len(result.get('errors') or [])}",
                flush=True,
            )
            return result
    except Exception as exc:
        _update_paper_run_record(paper_id, {
            "job_id": job_id,
            "kind": "assets",
            "status": "error",
            "actor": str(actor or ""),
            "finished_at": _now(),
            "step": "error",
            "error": str(exc),
        })
        raise


def _start_paper_collection_task(job: dict) -> dict:
    from contextvars import copy_context
    from management_portal import business_tasks
    from management_portal.paper_collector import require_actor

    if not schema_matches(job.get("collector_contract"), "tfi.paper.collector.v2"):
        raise RuntimeError("Collector job does not satisfy the current execution contract")
    require_actor(str(job.get("created_by") or ""))

    job_id = str(job.get("job_id") or "")
    def fail(exc: BaseException) -> None:
        current = _paper_collection_job_by_id(job_id) or {}
        if current.get("deleted") or current.get("status") in {"done", "error", "cancelled", "deleted"}:
            return
        _set_paper_collection_job(
            job_id,
            {
                "status": "error",
                "step": "error",
                "progress": 100,
                "error": str(exc)[:2000],
            },
        )

    try:
        business_tasks.start(
            f"paper-collection:{job_id}",
            copy_context().run,
            _run_paper_collection_job,
            job_id,
            on_error=fail,
        )
    except Exception as exc:
        return _set_paper_collection_job(
            job_id,
            {
                "status": "error",
                "step": "launch error",
                "progress": 100,
                "error": f"Failed to run PaperCollector business task: {exc}",
            },
        )
    return _paper_collection_job_by_id(job_id) or job


def _public_paper_collection_job(job: dict) -> dict:
    from management_portal.paper_projection import input_receipt

    result_count = len(_paper_collection_result_items(job))
    registered_count = int(job["registered_count"] if job.get("registered_count") is not None else result_count)
    invocation = job.get("invocation")
    return {
        "job_id": str(job.get("job_id") or ""),
        "direction": str(job.get("direction") or ""),
        "max_papers": int(job.get("max_papers") or 0),
        "status": str(job.get("status") or "queued"),
        "step": str(job.get("step") or ""),
        "progress": int(job.get("progress") or 0),
        "created_by": str(job.get("created_by") or ""),
        "created_at": int(job.get("created_at") or 0),
        "updated_at": int(job.get("updated_at") or 0),
        "registered_count": registered_count,
        "result_count": result_count,
        "invocation": input_receipt(invocation) if isinstance(invocation, dict) and invocation else None,
        "summary": str(job.get("summary") or ""),
        "error": str(job.get("error") or ""),
        "stdout_path": str(job.get("stdout_path") or ""),
        "stderr_path": str(job.get("stderr_path") or ""),
        "result_path": str(job.get("result_path") or ""),
    }


def _run_summary_job(
    paper_id: str,
    actor: str = "",
    job_id: str = "",
) -> None:
    paper_id = str(paper_id or "").strip()
    if not paper_id:
        raise ValueError("paper_id required")
    clean_job_id = str(job_id or "").strip()
    if clean_job_id:
        _update_paper_run_record(paper_id, {
            "job_id": clean_job_id,
            "kind": "summary",
            "status": "running",
            "step": "generating summary",
            "started_at": _now(),
            "error": "",
        })
    try:
        paper = _paper_by_id(paper_id)
        if not paper:
            raise KeyError("paper not found")
        print(f"[paper-summary] start {paper_id}", flush=True)
        path = _ensure_paper_summary(paper, actor=actor)
        if clean_job_id:
            _update_paper_run_record(paper_id, {
                "job_id": clean_job_id,
                "kind": "summary",
                "status": "done",
                "step": "done",
                "finished_at": _now(),
                "result_path": str(path or ""),
                "error": "",
            })
        print(f"[paper-summary] done {paper_id}: {path}", flush=True)
    except Exception as exc:
        if clean_job_id:
            _update_paper_run_record(paper_id, {
                "job_id": clean_job_id,
                "kind": "summary",
                "status": "error",
                "step": "error",
                "finished_at": _now(),
                "error": str(exc),
            })
        raise


def _run_paper_collection_job(job_id: str) -> None:
    from management_portal.paper_collector import run

    run(str(job_id or "").strip(), backend=sys.modules[__name__])


def _figure_image_path(paper: dict, figure: dict) -> Path | None:
    root = _paper_root(paper)
    rel_path = str(figure.get("image_rel_path") or "").strip()
    if not root or not rel_path:
        return None
    path = Path(rel_path)
    if not path.is_absolute():
        path = root / path
    try:
        resolved = path.resolve()
        if not resolved.is_relative_to(root.resolve()):
            return None
        return resolved if resolved.is_file() else None
    except (OSError, RuntimeError, ValueError):
        return None


def _run_figure_resident(prompt: str, paper: dict, figure: dict, kind: str, images: list[Path] | None = None, generated_id: str = "") -> tuple[str, dict]:
    """Explain a figure (``paper:figure``) or generate an image (``paper:figure-generation``) with the role Resident."""
    from management_portal.paper_projection import input_receipt, resume, turn

    paper_root = _paper_root(paper) or DATA_ROOT
    resident_role = "paper:figure" if kind == "explain" else "paper:figure-generation"
    runs_dir = paper_root / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    safe_figure_id = _slugify(str(figure.get("figure_id") or "figure"))
    run_id = f"figure_{_slugify(kind)}_{safe_figure_id}_{secrets.token_hex(4)}"
    output_path = runs_dir / f"{run_id}.md"
    events_path = runs_dir / f"{run_id}.events.jsonl"
    stderr_path = runs_dir / f"{run_id}.stderr.txt"

    actor = str(paper.get("created_by") or "portal")
    paper_id = str(paper.get("id") or paper.get("title") or "paper")
    operation = next((item for item in figure.get("generated", []) if item.get("generated_id") == generated_id), {}) if generated_id else figure
    binding_key = "invocation" if generated_id else "explanation_invocation"
    binding = operation.get(binding_key) or {}
    receipt = input_receipt(binding) if binding else {}
    input_message_id = str(receipt.get("input_message_id") or "")
    message_id = str(binding.get("requested_message_id") or input_message_id or f"msg_{secrets.token_hex(16)}")

    def remember(handle: dict) -> None:
        updates = {binding_key: {**input_receipt(handle), "actor": actor}}
        if generated_id:
            _set_figure_generated_item(paper_id, figure["figure_id"], generated_id, updates)
        else:
            _set_figure_fields(paper_id, figure["figure_id"], updates)

    if binding.get("resident_id") and (not input_message_id or binding.get("input_identity_status") == "unconfirmed"):
        raise RuntimeError("Figure input delivery is unconfirmed; inspect the original workflow; no input was resubmitted")
    input_media = []
    if images and not binding.get("resident_id"):
        import base64
        from management_portal.society_service import get_society_service
        from management_portal.workbench_projections.native import ensure

        sources = [Path(image).resolve(strict=True) for image in images]
        for source in sources:
            if not source.is_file() or not source.is_relative_to(paper_root.resolve(strict=True)):
                raise PermissionError("Figure input image is outside this paper's artifacts")
        identity = ensure("paper", actor=actor, resident_scope=resident_role, cwd=str(WORKSPACE_ROOT))
        for source in sources:
            uploaded = get_society_service().workspace.save_workspace_pasted_image({
                "resident_id": identity["resident_id"], "filename": source.name,
                "data_base64": base64.b64encode(source.read_bytes()).decode("ascii"),
            }, actor=actor)
            input_media.append({
                "media_type": "image", "source_type": "local_path",
                "source": uploaded["path"], "name": source.name,
                "mime_type": uploaded["content_type"],
            })
    if binding.get("resident_id") and input_message_id:
        result = resume(
            actor=str(binding.get("actor") or actor), resident_id=binding["resident_id"],
            lineage_id=str(binding.get("lineage_id") or ""), message_id=input_message_id, receipt=binding, on_submitted=remember,
        )
    else:
        result = turn(
            actor=actor, prompt=prompt, cwd=str(WORKSPACE_ROOT), resident_scope=resident_role,
            input_media=input_media, business_ref=run_id, message_id=message_id,
            on_assigned=remember, on_submitted=remember,
            metadata={"business_type": "paper_figure", "kind": kind},
        )
    identity = input_receipt(result["invocation"])
    atomic_replace_json(events_path, identity, indent=None, sort_keys=False)
    stderr_path.write_text("", encoding="utf-8")
    content = str(result.get("message") or "").strip()
    if not content:
        raise RuntimeError(f"{resident_role} returned empty output for figure {kind}")
    output_path.write_text(content, encoding="utf-8")
    usage = {
        **dict(result.get("usage") or {}),
        **identity,
        "kind": kind,
        "events_path": str(events_path),
        "stderr_path": str(stderr_path),
        "output_path": str(output_path),
    }
    return content, usage


def _authorize_paper_job_status(job: dict | None, *, actor: str, job_id: str, paper_id: str) -> None:
    if not str(actor or "").strip():
        raise PermissionError("Paper status requires an authenticated actor")
    if not job_id and not paper_id:
        raise ValueError("paper_id or job_id required")
    if job_id and not job:
        raise KeyError("Paper job not found")
    target_paper_id = str(job.get("paper_id") or "") if job else paper_id
    if not target_paper_id:
        raise KeyError("Paper job has no associated paper")
    get_resource_service().get(target_paper_id, actor=actor)
    if paper_id and target_paper_id != paper_id:
        raise ValueError("Paper job belongs to another paper")


def get_deep_read_status(*, actor: str, job_id: str = "", paper_id: str = "") -> dict:
    with LOCK:
        job = _latest_deep_read_job(paper_id=paper_id, job_id=job_id)
    _authorize_paper_job_status(job, actor=actor, job_id=job_id, paper_id=paper_id)
    if job and str(job.get("status") or "") in {"queued", "running"}:
        job = _launch_deep_read_business_task(job)
    return job or {"status": "idle", "paper_id": paper_id, "job_id": job_id}


# The sections a Deep Read analysis and an Exp extraction must contain. A reply with fewer than a third of them is not
# the requested artifact (for example a model narrating tool calls it cannot run) and is not saved as done; a long
# analysis cut off after its first sections still is.
DEEP_READ_SECTIONS = (
    "一段话先看懂", "论文要解决的问题", "方法结构", "关键机制与算法流程", "Figure / Table / Experiment Evidence",
    "关键结果", "可迁移价值与适用边界", "局限与需要继续确认的问题", "可检索标签",
)
EXP_SECTIONS = (
    "实验目标", "数据集 / 环境 / 任务", "Baselines 与对比对象", "Metrics", "训练与实现设置",
    "关键实验结果", "Ablation / Sensitivity", "复现路径", "复现风险",
)
DEEP_READ_HEADINGS = "\n".join(f"## {name}" for name in DEEP_READ_SECTIONS)


def _require_artifact_sections(content: str, sections: tuple[str, ...], artifact: str) -> None:
    headings = [
        re.sub(r"\s+", " ", line.strip().lstrip("#")).strip().casefold()
        for line in str(content or "").splitlines()
        if line.strip().startswith("#")
    ]
    present = [name for name in sections if any(name.casefold() in heading for heading in headings)]
    if len(present) * 3 < len(sections):
        opening = re.sub(r"\s+", " ", str(content or "")).strip()[:120]
        raise RuntimeError(
            f"{artifact} 返回的不是规定产物：{len(sections)} 个必需小节只出现 {len(present)} 个；输出开头：{opening}"
        )


def _run_deep_read_job(job_id: str) -> None:
    try:
        print(f"[paper-deep-read] {job_id} start", flush=True)
        job = _latest_deep_read_job(job_id=job_id)
        if not job:
            raise KeyError("Deep Read job not found")
        if str(job.get("status") or "") in {"done", "error"}:
            return
        paper_id = str(job.get("paper_id") or "")
        paper = get_paper_detail(paper_id)["paper"]
        actor = str(job.get("created_by") or "portal")
        _set_deep_read_job(
            job_id,
            {
                "status": "running",
                "step": "extracting pdf text",
                "progress": 18,
                "error": "",
            },
        )
        print(
            f"[paper-deep-read] {job_id} extracting pdf text",
            flush=True,
        )
        pdf_path = _local_pdf_for_paper(paper)
        text = _extract_pdf_text(pdf_path, paper_id=paper_id)
        if len(text.strip()) < 1200:
            raise RuntimeError("PDF text extraction returned too little text")
        from management_portal.paper_projection import input_receipt, resume, turn

        job = _latest_deep_read_job(job_id=job_id) or job
        resident_id = str(job.get("resident_id") or "")
        resident_input_message_id = str(job.get("resident_input_message_id") or "")
        attempt_handle = dict(job.get("resident_handoff") or {})

        def remember_input(handle: dict) -> None:
            nonlocal attempt_handle
            attempt_handle = input_receipt(handle)
            _set_deep_read_job(job_id, {
                "resident_id": attempt_handle["resident_id"],
                "resident_input_message_id": attempt_handle["input_message_id"],
                "resident_reply_message_ids": attempt_handle["reply_message_ids"],
                "resident_usage_message_id": attempt_handle["usage_message_id"],
                "resident_handoff": attempt_handle,
                "lineage_id": attempt_handle["lineage_id"],
            })

        if resident_id and attempt_handle.get("input_identity_status") == "unconfirmed":
            raise RuntimeError("Deep Read input delivery is unconfirmed; inspect the original workflow; no input was resubmitted")
        if resident_id and resident_input_message_id:
            result = resume(
                actor=actor,
                resident_id=resident_id,
                lineage_id=str(job.get("lineage_id") or ""),
                message_id=resident_input_message_id,
                receipt=attempt_handle,
                on_submitted=remember_input,
            )
        else:
            _set_deep_read_job(job_id, {"status": "running", "step": "waiting for Resident", "progress": 42})
            result = turn(
                actor=actor,
                prompt=_deep_read_prompt(paper, text),
                resident_scope="paper:deep-read",
                business_ref=f"paper-deep-read:{job_id}",
                metadata={
                    "business_type": "paper_deep_read",
                    "paper_id": paper_id,

                },
                on_assigned=remember_input,
                on_submitted=remember_input,
            )
        content = str(result.get("message") or "").strip()
        if not content:
            raise RuntimeError("Deep Read AI execution returned empty output")
        _require_artifact_sections(content, DEEP_READ_SECTIONS, "Deep Read")
        usage = (
            dict(result.get("usage"))
            if isinstance(result.get("usage"), dict)
            else {}
        )
        analysis = _deep_read_analysis_document(paper, content, usage)
        print(f"[paper-deep-read] {job_id} writing analysis", flush=True)
        _set_deep_read_job(job_id, {"step": "writing analysis", "progress": 82})
        analysis_path = _write_analysis_file(paper, analysis, usage)
        _update_paper_fields(
            paper_id,
            {
                "analysis_path": str(analysis_path),
                "status": "analysis_ready",
            },
        )
        _set_deep_read_job(
            job_id,
            {
                "status": "done",
                "step": "done",
                "progress": 100,
                "analysis_path": str(analysis_path),
                "usage": usage,
                "error": "",
            },
        )
        print(f"[paper-deep-read] {job_id} done", flush=True)
    except Exception as exc:
        print(f"[paper-deep-read] {job_id} error: {exc}", flush=True)
        _set_deep_read_job(
            job_id,
            {
                "status": "error",
                "step": "error",
                "error": str(exc),
                "progress": 100,
            },
        )


def _local_pdf_for_paper(paper: dict) -> Path:
    pdf_path = str(paper.get("pdf_path") or "").strip()
    if _is_url(pdf_path):
        cache_dir = DATA_ROOT / "pdf_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        parsed = urlparse(pdf_path)
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(parsed.path).name or str(paper.get("id") or "paper"))
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        path = cache_dir / name
        if not path.is_file() or path.stat().st_size < 20_000:
            req = urllib.request.Request(pdf_path, headers={"User-Agent": "TFIPortalPaperDeepRead/0.1"})
            with urllib.request.urlopen(req, timeout=270) as response:
                data = response.read()
            if not data.startswith(b"%PDF"):
                raise RuntimeError("remote PDF did not return PDF bytes")
            path.write_bytes(data)
        return path
    path = _safe_file_path(pdf_path)
    if not path or not path.is_file():
        raise FileNotFoundError("PDF not found")
    return path


def _extract_pdf_text(pdf_path: Path, *, paper_id: str) -> str:
    paper = next((item for item in _all_papers() if str(item.get("id") or "") == paper_id), {})
    text_path = _paper_store_file(paper, "pdf_text_clean") if paper else None
    if not text_path:
        PDF_TEXT_ROOT.mkdir(parents=True, exist_ok=True)
        text_path = PDF_TEXT_ROOT / f"{_slugify(paper_id)}.txt"
    return _extract_pdf_text_file(pdf_path, cache_path=text_path)


def _extract_pdf_text_file(pdf_path: Path, *, cache_path: Path | None = None) -> str:
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if cache_path.is_file() and cache_path.stat().st_size > 1000:
            return cache_path.read_text(encoding="utf-8", errors="replace")
    proc = process_runtime.run_process(
        [_poppler_command("pdftotext"), "-layout", "-enc", "UTF-8", str(pdf_path), "-"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=360,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"pdftotext failed: {stderr}")
    text = proc.stdout.decode("utf-8", errors="replace")
    text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
    if cache_path:
        cache_path.write_text(text, encoding="utf-8")
    return text


def _extract_pdf_text_raw(pdf_path: Path, *, paper_id: str) -> str:
    paper = next((item for item in _all_papers() if str(item.get("id") or "") == paper_id), {})
    text_path = _paper_store_file(paper, "pdf_text_raw") if paper else None
    if not text_path:
        PDF_TEXT_ROOT.mkdir(parents=True, exist_ok=True)
        text_path = PDF_TEXT_ROOT / f"{_slugify(paper_id)}.raw.txt"
    text_path.parent.mkdir(parents=True, exist_ok=True)
    if text_path.is_file() and text_path.stat().st_size > 1000:
        return text_path.read_text(encoding="utf-8", errors="replace")
    proc = process_runtime.run_process(
        [_poppler_command("pdftotext"), "-raw", "-enc", "UTF-8", str(pdf_path), "-"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=360,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"pdftotext raw failed: {stderr}")
    text = proc.stdout.decode("utf-8", errors="replace")
    text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
    text_path.write_text(text, encoding="utf-8")
    return text


def _deep_read_prompt(paper: dict, pdf_text: str) -> str:
    full_text = str(pdf_text or "")
    return f"""请深读下面这篇论文，输出 Markdown。必须包含这些小节：

# {paper.get('title') or 'Paper analysis'}

{DEEP_READ_HEADINGS}

只根据论文证据讨论适用场景，不假定论文属于某个项目，不虚构项目需求。

Paper metadata:
- Title: {paper.get('title') or ''}
- Authors: {paper.get('authors') or ''}
- Venue: {paper.get('venue') or ''} {paper.get('year') or ''}
- Tags: {', '.join(paper.get('tags') or [])}
- Abstract: {paper.get('abstract') or ''}

Extracted PDF text:
{full_text}
"""


def _deep_read_analysis_document(
    paper: dict,
    content: str,
    usage: dict,
) -> str:
    header = (
        "---\n"
        "schema: tfi.paper_analysis.v1\n"
        f"paper_id: {paper.get('id') or ''}\n"
        f"generated_at: {_now()}\n"
        f"llm_api: {usage.get('api_id') or ''}\n"
        f"llm_model: {usage.get('model') or ''}\n"
        "---\n\n"
    )
    return header + _strip_outer_markdown_fence(content).strip() + "\n"


def _strip_outer_markdown_fence(content: str) -> str:
    text = str(content or "").strip()
    match = re.fullmatch(r"```(?:markdown|md)?\s*\n([\s\S]*?)\n```\s*", text, flags=re.I)
    if match:
        return match.group(1).strip()
    frontmatter = re.fullmatch(r"(---\n[\s\S]*?\n---\n*)([\s\S]*)", text)
    if frontmatter:
        body = str(frontmatter.group(2) or "").strip()
        body_match = re.fullmatch(r"```(?:markdown|md)?\s*\n([\s\S]*?)\n```\s*", body, flags=re.I)
        if body_match:
            return frontmatter.group(1).rstrip() + "\n\n" + body_match.group(1).strip()
    return text


def _resident_reply(
    prompt: str,
    *,
    actor: str,
    resident_scope: str,
    cwd: str = "",
    metadata: dict | None = None,
) -> tuple[str, dict]:
    """One task to the Paper role Resident of ``resident_scope``: its reply and usage.

    The role Resident runs the task with its own Provider, model and settings.
    """
    from management_portal.paper_projection import turn

    result = turn(
        actor=str(actor or "portal"),
        prompt=prompt,
        resident_scope=resident_scope,
        cwd=cwd,
        metadata=dict(metadata or {"business_type": "paper"}),
    )
    content = str(result.get("message") or "").strip()
    if not content:
        raise ValueError(f"{resident_scope} returned an empty reply")
    usage = dict(result.get("usage")) if isinstance(result.get("usage"), dict) else {}
    return content, usage


def _write_analysis_file(paper: dict, content: str, usage: dict) -> Path:
    path = _paper_store_file(paper, "analysis")
    if not path:
        ANALYSIS_ROOT.mkdir(parents=True, exist_ok=True)
        path = ANALYSIS_ROOT / f"{_slugify(str(paper.get('id') or paper.get('title') or 'paper'))}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_strip_outer_markdown_fence(content).strip() + "\n", encoding="utf-8")
    usage_path = path.with_suffix(".usage.json")
    atomic_replace_json(usage_path, usage, indent=2, sort_keys=False)
    return path


def _summary_text(paper: dict, limit: int = 2000) -> str:
    path = _paper_store_file(paper, "summary")
    return _read_text_preview(path, limit=limit).strip()


def _ensure_paper_summary(paper: dict, *, actor: str = "", force: bool = False) -> Path | None:
    path = _paper_store_file(paper, "summary")
    if not path:
        return None
    if path.is_file() and path.read_text(encoding="utf-8", errors="replace").strip() and not force:
        return path
    summary, usage = _generate_paper_summary(paper, actor=actor)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(summary.strip() + "\n", encoding="utf-8")
    usage_payload = {
        **usage,
        "paper_id": str(paper.get("id") or ""),
        "generated_by": actor,
        "generated_at": _now(),
    }
    atomic_replace_json(path.with_suffix(".usage.json"), usage_payload, indent=2, sort_keys=False)
    return path


def _generate_paper_summary(paper: dict, *, actor: str) -> tuple[str, dict]:
    abstract = str(paper.get("abstract") or "").strip()
    source_text = abstract or _analysis_text(paper, limit=1800).strip() or _pdf_text_for_paper(paper)[:1800].strip()
    if not source_text:
        raise ValueError("paper summary requires an abstract, analysis, or readable PDF text")
    prompt = f"""请用一段中文总结这篇论文，80-160 个中文字，不要标题、列表或 Markdown。说明研究问题、核心方法和适合继续阅读的场景。
如实保留摘要材料对证据性质的限定（例如合成数据、示例、非真实论文、未经验证的结果），不要把这类内容写成实测结论或已验证的事实。

标题：{paper.get('title') or ''}
作者：{paper.get('authors') or ''}
来源：{paper.get('venue') or ''} {paper.get('year') or ''}
摘要材料：{source_text}
"""
    content, usage = _resident_reply(prompt, actor=actor, resident_scope="paper:summary")
    return _clean_summary_paragraph(content), usage


def _clean_summary_paragraph(content: str) -> str:
    text = str(content or "").strip()
    text = re.sub(r"^```(?:markdown|md)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    text = re.sub(r"^#+\s*.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text).strip(" -\n\t")
    if len(text) > 700:
        text = text[:700].rstrip(" ，,。.;；") + "。"
    if not text:
        raise ValueError("empty paper summary")
    return text


def _analysis_text(paper: dict, limit: int = 80_000) -> str:
    path = _safe_file_path(str(paper.get("analysis_path") or ""))
    text = _read_text_preview(path, limit=limit)
    if text:
        return text
    return str(paper.get("abstract") or "")


def start_review(payload: dict, actor: str) -> dict:
    from management_portal import business_tasks

    paper_id = str(payload.get("paper_id") or "").strip()
    if not paper_id:
        raise ValueError("paper_id required")
    clean_actor = str(actor or "").strip()
    for previous in paper_state_store.records("review_jobs", paper_id=paper_id):
        if previous.get("created_by") == clean_actor:
            _settle_interrupted_review(previous)
    with LOCK:
        papers = _all_papers()
        paper = next((p for p in papers if p["id"] == paper_id), None)
        if not paper:
            raise KeyError("paper not found")
        with paper_state_store.locked("review_jobs"):
            active = next(
                (
                    item
                    for item in reversed(paper_state_store.records("review_jobs", paper_id=paper_id))
                    if str(item.get("created_by") or "") == clean_actor
                    and str(item.get("status") or "")
                    in {"queued", "running"}
                ),
                None,
            )
            if active is not None:
                return active
            known_ids = [
                item.get("review_id")
                for item in _all_reviews()
            ]
            known_ids.extend(
                item.get("review_id")
                for item in _all_review_jobs()
            )
            review_id = get_resource_service().new_id(
                "review",
                known_ids,
            )
            job = {
                "job_id": f"paper-review-{secrets.token_hex(6)}",
                "paper_id": paper_id,
                "paper_title": str(paper.get("title") or ""),
                "review_id": review_id,
                "title": str(payload.get("title") or ""),
                "status": "queued",
                "step": "queued",
                "process_owner": business_tasks.process_identity(),
                "created_by": clean_actor,
                "created_at": _now(),
                "updated_at": _now(),
                "error": "",
            }
            paper_state_store.append("review_jobs", job)
    return _start_review_task(job)


def _start_review_task(job: dict) -> dict:
    from management_portal import business_tasks

    job_id = str(job.get("job_id") or "")
    def fail(exc: BaseException) -> None:
        _set_review_job(job_id, {
            "status": "error",
            "step": "error",
            "error": str(exc)[:2000],
        })

    try:
        business_tasks.start(
            f"paper-review:{job_id}",
            _run_review_job,
            job_id,
            on_error=fail,
        )
    except Exception as exc:
        return _set_review_job(job_id, {
            "status": "error",
            "step": "launch error",
            "error": f"Failed to run Paper review business task: {exc}",
        })
    return _review_job_by_id(job_id) or job


def _run_review_job(job_id: str) -> dict:
    job = _review_job_by_id(job_id)
    if not job:
        raise KeyError("Paper review job not found")
    _set_review_job(job_id, {
        "status": "running",
        "step": "generating review",
        "error": "",
    })
    try:
        paper_id = str(job.get("paper_id") or "")
        papers = [dict(item) for item in _all_papers()]
        paper = next(
            (item for item in papers if item["id"] == paper_id),
            None,
        )
        if not paper:
            raise KeyError("paper not found")
        review = _generate_ai_review(
            dict(paper),
            papers,
            actor=str(job.get("created_by") or ""),
            title=str(job.get("title") or ""),
            review_id=str(job.get("review_id") or ""),
        )
        _review_markdown_path(review)
        with LOCK:
            with paper_state_store.locked("reviews"):
                if paper_state_store.get("reviews", str(review.get("review_id") or "")) is None:
                    paper_state_store.append("reviews", review)
            _write_review_markdown(review)
        _announce_review_changes()
        incomplete = review.get("status") == "ai_incomplete"
        _set_review_job(job_id, {
            "status": "error" if incomplete else "done",
            "step": "incomplete review" if incomplete else "done",
            "error": str(review.get("error") or "") if incomplete else "",
        })
        return review
    except Exception as exc:
        _set_review_job(job_id, {
            "status": "error",
            "step": "error",
            "error": str(exc)[:2000],
        })
        raise


def _settle_interrupted_review(job: dict) -> dict:
    from management_portal import business_tasks

    if job.get("status") not in {"queued", "running"} or business_tasks.process_is_alive(job.get("process_owner") or {}):
        return job
    # The owner may have saved the review just before it died. Recheck under
    # the same locks as completion and recover that result without another AI call.
    with LOCK, paper_state_store.locked("review_jobs", "reviews"):
        current = paper_state_store.get("review_jobs", job.get("job_id"))
        if current is None:
            return job
        if current.get("status") not in {"queued", "running"} or current.get("process_owner") != job.get("process_owner"):
            return current
        review = paper_state_store.get("reviews", current.get("review_id"))
        complete = review is not None and review.get("status") != "ai_incomplete"
        current = paper_state_store.merge("review_jobs", current["job_id"], {
            "status": "done" if complete else "error",
            "step": "done" if complete else "interrupted",
            "error": "" if complete else str((review or {}).get("error") or "Paper review task was interrupted by a process restart. Please generate the review again."),
            "updated_at": _now(),
        })
    _notify_paper_projection(current.get("paper_id"), actors=[current.get("created_by")], fields=["review"])
    return current


def review_job_paper_id(job_id: str) -> str:
    """The paper a review job belongs to, so a read by job_id is checked against that paper."""
    job = _review_job_by_id(job_id)
    if not job:
        raise KeyError("Paper review job not found")
    return str(job.get("paper_id") or "")


def get_review_status(*, actor: str, job_id: str = "", paper_id: str = "") -> dict:
    if job_id:
        job = _review_job_by_id(job_id)
        if not job:
            raise KeyError("Paper review job not found")
    elif paper_id:
        job = next((item for item in reversed(paper_state_store.records("review_jobs", paper_id=paper_id))
                    if str(item.get("created_by") or "") == str(actor or "")), None)
    else:
        raise ValueError("paper_id or job_id required")
    if job:
        if not is_user_or_resident(actor):
            raise PermissionError("Paper review job needs a User or Resident")
        if paper_id and str(job.get("paper_id") or "") != paper_id:
            raise ValueError("Paper review job belongs to another paper")
        job = _settle_interrupted_review(job)
    return {"job": job}


def wait_review(
    job_id: str,
    *,
    actor: str,
    timeout_seconds: float = 5400,
) -> dict:
    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    while time.monotonic() < deadline:
        job = _review_job_by_id(job_id)
        if not job:
            raise KeyError("Paper review job not found")
        if not is_user_or_resident(actor):
            raise PermissionError("Paper review job needs a User or Resident")
        job = _settle_interrupted_review(job)
        status = str(job.get("status") or "")
        if status == "done":
            result = job.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("Paper review job result is invalid")
            return dict(result)
        if status == "error":
            raise RuntimeError(
                str(job.get("error") or "Paper review failed")
            )
        time.sleep(0.2)
    raise TimeoutError(
        f"Paper review job did not finish: {job_id}"
    )


def create_review(payload: dict, actor: str) -> dict:
    job = start_review(payload, actor)
    if str(job.get("status") or "") == "done":
        result = job.get("result")
        return dict(result) if isinstance(result, dict) else {}
    return wait_review(
        str(job.get("job_id") or ""),
        actor=actor,
    )


def create_exp(payload: dict, actor: str) -> dict:
    return start_exp(payload, actor)


def start_exp(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    if not paper_id:
        raise ValueError("paper_id required")
    with LOCK:
        papers = _all_papers()
        paper = next((p for p in papers if p["id"] == paper_id), None)
        if not paper:
            raise KeyError("paper not found")
        paper = dict(paper)
        with paper_state_store.locked("exp_jobs"):
            latest = _latest_exp_job(paper_id=paper_id)
            if latest and str(latest.get("status") or "") in {"queued", "running"}:
                if _is_stale_exp_job(latest):
                    paper_state_store.merge("exp_jobs", latest["job_id"], {
                        "status": "error",
                        "step": "stale",
                        "progress": 100,
                        "updated_at": _now(),
                        "error": (
                            "Exp task was interrupted or stopped updating. "
                            "Please generate Exp again."
                        ),
                    })
                    latest = None
                else:
                    return latest
            job_id = f"exp-{secrets.token_hex(6)}"
            job = {
                "job_id": job_id,
                "paper_id": paper_id,
                "paper_title": str(paper.get("title") or ""),
                "status": "queued",
                "step": "queued",
                "progress": 0,
                "created_by": actor,
                "created_at": _now(),
                "updated_at": _now(),
                "exp_path": "",
                "error": "",
                "usage": {},
            }
            paper_state_store.append("exp_jobs", job)
    return _start_exp_task(job)


def get_exp_status(*, actor: str, job_id: str = "", paper_id: str = "") -> dict:
    with LOCK:
        job = _latest_exp_job(paper_id=paper_id, job_id=job_id)
        _authorize_paper_job_status(job, actor=actor, job_id=job_id, paper_id=paper_id)
        if job and _is_stale_exp_job(job):
            job = _mark_stale_exp_job(job)
    return job or {"status": "idle", "paper_id": paper_id, "job_id": job_id}


def _start_exp_task(job: dict) -> dict:
    from management_portal import business_tasks

    job_id = str(job.get("job_id") or "")
    def fail(exc: BaseException) -> None:
        _set_exp_job(
            job_id,
            {
                "status": "error",
                "step": "error",
                "progress": 100,
                "error": str(exc)[:2000],
            },
        )

    try:
        business_tasks.start(
            f"paper-exp:{job_id}",
            _run_exp_job,
            job_id,
            on_error=fail,
        )
    except Exception as exc:
        return _set_exp_job(
            job_id,
            {
                "status": "error",
                "step": "launch error",
                "progress": 100,
                "error": f"Failed to run Exp business task: {exc}",
            },
        )
    return _latest_exp_job(job_id=job_id) or job


def _run_exp_job(job_id: str) -> None:
    try:
        print(f"[paper-exp] {job_id} start", flush=True)
        job = _set_exp_job(job_id, {"status": "running", "step": "loading paper", "progress": 8})
        paper_id = str(job.get("paper_id") or "")
        paper = get_paper_detail(paper_id)["paper"]
        print(f"[paper-exp] {job_id} calling llm", flush=True)
        _set_exp_job(job_id, {"step": "calling llm", "progress": 35})
        content, usage = _generate_exp_extraction(paper, actor=str(job.get("created_by") or ""))
        _require_artifact_sections(content, EXP_SECTIONS, "Exp")
        print(f"[paper-exp] {job_id} writing exp", flush=True)
        _set_exp_job(job_id, {"step": "writing exp", "progress": 82})
        path = _write_exp_markdown(paper, content, usage, actor=str(job.get("created_by") or ""))
        PAPER_STORE.update_paper(paper_id, {})
        _set_exp_job(
            job_id,
            {
                "status": "done",
                "step": "done",
                "progress": 100,
                "exp_path": str(path),
                "usage": usage,
                "error": "",
            },
        )
        print(f"[paper-exp] {job_id} done", flush=True)
    except Exception as exc:
        print(f"[paper-exp] {job_id} error: {exc}", flush=True)
        _set_exp_job(job_id, {"status": "error", "step": "error", "error": str(exc), "progress": 100})


def _paper_by_id(paper_id: str) -> dict | None:
    return PAPER_STORE.paper_by_id(str(paper_id or ""))


def _paper_for_asset_lookup(paper_id: str) -> dict | None:
    return _paper_by_id(str(paper_id or "").strip())


def _review_markdown_path(review: dict) -> Path:
    paper = _paper_by_id(str(review.get("paper_id") or ""))
    reviews_dir = _paper_store_file(paper or {}, "reviews_dir") if paper else None
    if not reviews_dir:
        raise ValueError("review paper has no reviews directory")
    review_id = str(review.get("review_id") or "").strip()
    get_resource_service().validate_id(review_id, "review")
    return reviews_dir / f"{review_id}.md"


def _write_review_markdown(review: dict) -> None:
    path = _review_markdown_path(review)
    path.parent.mkdir(parents=True, exist_ok=True)
    body_parts = [
        "---",
        f"review_id: {review.get('review_id') or ''}",
        f"paper_id: {review.get('paper_id') or ''}",
        f"created_by: {review.get('created_by') or ''}",
        f"created_at: {review.get('created_at') or ''}",
        f"status: {review.get('status') or ''}",
        "---",
        "",
        f"# {review.get('title') or 'Review'}",
        "",
        str(review.get("summary") or ""),
        "",
    ]
    for reviewer in review.get("reviewers") or []:
        if isinstance(reviewer, dict):
            body_parts.extend([f"## {reviewer.get('title') or reviewer.get('id') or 'Reviewer'}", "", str(reviewer.get("body") or ""), ""])
    if review.get("author_action_memo"):
        body_parts.extend(["## Author Action Memo", "", str(review.get("author_action_memo") or ""), ""])
    if review.get("raw_response"):
        body_parts.extend(["## 未完成评审的原始输出", "", str(review["raw_response"]), ""])
    path.write_text("\n".join(body_parts), encoding="utf-8")


def _write_exp_markdown(paper: dict, content: str, usage: dict, *, actor: str = "") -> Path:
    exp_path = _paper_store_file(paper, "exp")
    if not exp_path:
        root_text = str(paper.get("_store_root") or "").strip()
        if not root_text:
            raise ValueError("paper store root unavailable")
        root = Path(root_text)
        exp_path = root / "exp.md"
    exp_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "---\n"
        "schema: tfi.paper_exp.v1\n"
        f"paper_id: {paper.get('id') or ''}\n"
        f"generated_by: {actor or ''}\n"
        f"generated_at: {_now()}\n"
        f"llm_api: {usage.get('api_id') or ''}\n"
        f"llm_model: {usage.get('model') or ''}\n"
        "---\n\n"
    )
    exp_path.write_text(header + str(content or "").strip() + "\n", encoding="utf-8")
    return exp_path


def _related_papers(paper: dict, papers: list[dict]) -> list[dict]:
    tags = set()
    for key in ("tags", "workload", "platform", "architecture", "optimization"):
        tags.update(str(x).casefold() for x in paper.get(key) or [])
    scored = []
    for other in papers:
        if other["id"] == paper["id"]:
            continue
        other_tags = set()
        for key in ("tags", "workload", "platform", "architecture", "optimization"):
            other_tags.update(str(x).casefold() for x in other.get(key) or [])
        score = len(tags & other_tags)
        if score:
            scored.append((score, other))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [{"id": p["id"], "title": p["title"], "venue": p.get("venue", ""), "year": p.get("year", "")} for _, p in scored[:5]]


def _generate_exp_extraction(paper: dict, *, actor: str) -> tuple[str, dict]:
    analysis = _analysis_text(paper, limit=120_000)
    pdf_text = _pdf_text_for_paper(paper)
    if not (analysis.strip() or pdf_text.strip() or str(paper.get("abstract") or "").strip()):
        raise ValueError("paper has no analysis, PDF text, or abstract for Exp extraction")
    prompt = f"""请为下面论文生成 Experiment Extraction，标题用：
# Exp: {paper.get('title') or 'Paper'}

必须包含这些小节：

## 实验目标
说明实验要验证什么 claim。

## 数据集 / 环境 / 任务
列出 dataset、simulator、robot、benchmark、task suite、train/test split。没有就写未说明。

## Baselines 与对比对象
列出 baseline、ablation、human/heuristic/oracle、previous SOTA。

## Metrics
列出评价指标、成功率定义、速度/延迟/成本指标、统计方式。

## 训练与实现设置
列出模型规模、训练步数、batch、optimizer、硬件、代码依赖、关键超参。没有就写未说明。

## 关键实验结果
用短表格或 bullet 记录最重要结果，标出结果支持了哪些 claim。

## Ablation / Sensitivity
提取消融、敏感性实验和反例。

## 复现路径
给出最小复现实验步骤，包含输入数据、核心代码模块、检查点、运行命令可能需要哪些信息。

## 复现风险
列出最可能卡住的地方，以及需要向作者或原文确认的信息。

Paper metadata:
- Title: {paper.get('title') or ''}
- Authors: {paper.get('authors') or ''}
- Venue: {paper.get('venue') or ''} {paper.get('year') or ''}
- Tags: {', '.join(paper.get('tags') or [])}
- Abstract: {paper.get('abstract') or ''}

Analysis markdown:
{analysis}

Full extracted PDF text:
{pdf_text}
"""
    content, usage = _resident_reply(prompt, actor=actor, resident_scope="paper:exp")
    return str(content or "").strip(), usage


def _generate_ai_review(
    paper: dict,
    papers: list[dict],
    *,
    actor: str,
    review_id: str,
    title: str = "",
) -> dict:
    analysis = _analysis_text(paper)
    pdf_text = _pdf_text_for_paper(paper)
    if not (analysis.strip() or pdf_text.strip() or str(paper.get("abstract") or "").strip()):
        raise ValueError("paper has no analysis, PDF text, or abstract for AI review")
    related = _related_papers(paper, papers)
    prompt = f"""请对下面论文生成 AI review。输出 JSON，字段固定为：
{{
  "title": "简短 review 标题",
  "overall_recommendation": "Strong Accept | Accept | Weak Accept | Borderline | Weak Reject | Reject",
  "confidence": "High | Medium | Low",
  "summary": "3-5 句中文总结",
  "reviewers": [
    {{"id": "A", "body": "Markdown，Reviewer A: Novelty & Contribution"}},
    {{"id": "B", "body": "Markdown，Reviewer B: Method & Soundness"}},
    {{"id": "C", "body": "Markdown，Reviewer C: Evaluation & Fairness"}}
  ],
  "author_action_memo": "Markdown，给作者/研究者的下一步行动建议"
}}

每个 reviewer 的 Markdown 必须包含：
### Summary
### Strengths
### Major Concerns
### Questions
### Evidence To Check

每个 reviewer 控制在 600-900 个中文字。author_action_memo 控制在 400-700 个中文字。

Paper metadata:
- Title: {paper.get('title') or ''}
- Authors: {paper.get('authors') or ''}
- Venue: {paper.get('venue') or ''} {paper.get('year') or ''}
- Tags: {', '.join(paper.get('tags') or [])}
- Abstract: {paper.get('abstract') or ''}

Related papers in local library:
{json.dumps(related, ensure_ascii=False, indent=2)}

Analysis markdown:
{analysis}

Full extracted PDF text:
{pdf_text}
"""
    content, usage = _resident_reply(prompt, actor=actor, resident_scope="paper:review")
    try:
        parsed = _extract_json_object(content)
        return _coerce_ai_review(
            parsed,
            raw_content=content,
            paper=paper,
            related=related,
            usage=usage,
            actor=actor,
            review_id=review_id,
            title=title,
        )
    except ValueError as exc:
        return _fallback_ai_review_from_text(
            content,
            paper=paper,
            related=related,
            usage=usage,
            actor=actor,
            review_id=review_id,
            title=title,
            error=str(exc),
        )


def _pdf_text_for_paper(paper: dict) -> str:
    try:
        return _extract_pdf_text(_local_pdf_for_paper(paper), paper_id=str(paper.get("id") or ""))
    except Exception:
        return ""


def _extract_json_object(content: str) -> dict:
    text = str(content or "").strip()
    decoder = json.JSONDecoder()
    fenced = [
        match.group(1).strip()
        for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    ]
    for candidate in [*fenced, text]:
        for start, character in enumerate(candidate):
            if character != "{":
                continue
            try:
                value, _ = decoder.raw_decode(candidate, start)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    raise ValueError("AI review response was not a JSON object")


def _coerce_ai_review(
    parsed: dict,
    *,
    raw_content: str,
    paper: dict,
    related: list[dict],
    usage: dict,
    actor: str,
    review_id: str,
    title: str,
) -> dict:
    for field in ("title", "overall_recommendation", "confidence", "summary", "author_action_memo"):
        if not isinstance(parsed.get(field), str) or not parsed[field].strip():
            raise ValueError(f"AI review response missing required text: {field}")
    by_id = {}
    reviewers_raw = parsed.get("reviewers")
    if not isinstance(reviewers_raw, list):
        raise ValueError("AI review response missing reviewers")
    expected_ids = {str(lens["id"]) for lens in REVIEW_LENSES}
    for item in reviewers_raw:
        if not isinstance(item, dict):
            raise ValueError("AI review reviewer must be an object")
        key = str(item.get("id") or "").strip()
        body = item.get("body")
        if key not in expected_ids or key in by_id or not isinstance(body, str) or not body.strip():
            raise ValueError("AI review reviewers must contain unique A/B/C with nonempty bodies")
        by_id[key] = body.strip()
    if set(by_id) != expected_ids:
        raise ValueError("AI review response missing one or more reviewers")
    reviewers = [{**lens, "body": by_id[str(lens["id"])]} for lens in REVIEW_LENSES]
    return {
        "review_id": review_id,
        "paper_id": paper["id"],
        "title": str(title or parsed.get("title") or f"{paper['title']} AI review").strip()[:180],
        "created_by": actor,
        "created_at": _now(),
        "status": "ai_generated",
        "overall_recommendation": str(parsed.get("overall_recommendation") or "").strip(),
        "confidence": str(parsed.get("confidence") or "").strip(),
        "summary": str(parsed.get("summary") or "").strip(),
        "library_check": {
            "closest_papers": related,
            "analysis_excerpt": _analysis_text(paper, limit=1600)[:900],
        },
        "reviewers": reviewers,
        "author_action_memo": str(parsed.get("author_action_memo") or "").strip(),
        "usage": usage,
        "source": "kimi_ai_review",
    }


def _fallback_ai_review_from_text(
    content: str,
    *,
    paper: dict,
    related: list[dict],
    usage: dict,
    actor: str,
    review_id: str,
    title: str,
    error: str = "AI review response was not a complete JSON review",
) -> dict:
    body = str(content or "")
    return {
        "review_id": review_id,
        "paper_id": paper["id"],
        "title": str(title or f"{paper['title']} AI review").strip()[:180],
        "created_by": actor,
        "created_at": _now(),
        "status": "ai_incomplete",
        "overall_recommendation": "",
        "confidence": "",
        "summary": "评审未完成：模型未返回完整的结构化评审。原始输出已保留，不作为三位 Reviewer 的有效评审。",
        "error": error,
        "raw_response": body,
        "library_check": {
            "closest_papers": related,
            "analysis_excerpt": _analysis_text(paper, limit=1600)[:900],
        },
        "reviewers": [],
        "author_action_memo": "",
        "usage": usage,
        "source": "kimi_ai_review_unstructured",
    }


def list_reviews(paper_id: str = "") -> dict:
    reviews = paper_state_store.records("reviews", paper_id=paper_id) if paper_id else _all_reviews()
    return {"reviews": sorted(reviews, key=lambda item: int(item.get("created_at") or 0), reverse=True)}


def delete_review(payload: dict, actor: str) -> dict:
    review_id = str(payload.get("review_id") or "").strip()
    if not review_id:
        raise ValueError("review_id required")
    with LOCK:
        target = paper_state_store.get("reviews", review_id)
        if not target:
            raise KeyError("review not found")
        from .workbench_projections.resource_lifecycle import release_resource
        release_resource(review_id, actor=actor)
        _delete_bundle_record("reviews", review_id, _review_markdown_path(target))
    _announce_review_changes()
    return {
        "deleted": True,
        "review_id": review_id,
        "paper_id": str(target.get("paper_id") or ""),
        "deleted_by": actor,
        "deleted_at": _now(),
    }


def create_note(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    body = str(payload.get("body") or payload.get("content") or "").strip()
    if not paper_id:
        raise ValueError("paper_id required")
    if not body:
        raise ValueError("note body required")
    with LOCK:
        papers = _all_papers()
        if not any(str(p.get("id") or "") == paper_id for p in papers):
            raise KeyError("paper not found")
        note = {
            "note_id": f"note-{secrets.token_hex(6)}",
            "paper_id": paper_id,
            "body": body,
            "created_by": actor,
            "created_at": _now(),
        }
        paper_state_store.append("notes", note)
        _write_note_markdown(note)
    return note


def _write_note_markdown(note: dict) -> None:
    path = _note_markdown_path(note)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join([
        "---",
        f"note_id: {note.get('note_id') or ''}",
        f"paper_id: {note.get('paper_id') or ''}",
        f"created_by: {note.get('created_by') or ''}",
        f"created_at: {note.get('created_at') or ''}",
        "---",
        "",
        str(note.get("body") or ""),
        "",
    ])
    path.write_text(body, encoding="utf-8")


def _note_markdown_path(note: dict) -> Path | None:
    paper = _paper_by_id(str(note.get("paper_id") or ""))
    notes_dir = _paper_store_file(paper or {}, "notes_dir") if paper else None
    if not notes_dir:
        return None
    note_id = _slugify(str(note.get("note_id") or f"note-{secrets.token_hex(4)}"))
    return notes_dir / f"{note_id}.md"


def _delete_bundle_record(
    kind: str,
    record_id: str,
    artifact_path: Path | None,
) -> None:
    artifact_content = (
        artifact_path.read_bytes()
        if artifact_path is not None and artifact_path.is_file()
        else None
    )
    if artifact_content is not None:
        artifact_path.unlink()
    try:
        paper_state_store.delete(kind, record_id)
    except Exception:
        if artifact_path is not None and artifact_content is not None:
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_bytes(artifact_content)
        raise


def delete_note(payload: dict, actor: str) -> dict:
    note_id = str(payload.get("note_id") or "").strip()
    if not note_id:
        raise ValueError("note_id required")
    with LOCK:
        target = paper_state_store.get("notes", note_id)
        if not target:
            raise KeyError("note not found")
        _delete_bundle_record("notes", note_id, _note_markdown_path(target))
    return {"deleted": True, "note_id": note_id, "paper_id": str(target.get("paper_id") or ""), "deleted_by": actor, "deleted_at": _now()}


def extract_refs(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    if not paper_id:
        raise ValueError("paper_id required")
    with LOCK:
        papers = _all_papers()
        paper = next((item for item in papers if str(item.get("id") or "") == paper_id), None)
        if not paper:
            raise KeyError("paper not found")
        public_papers = [_paper_public(item) for item in papers]
    text = _extract_pdf_text_raw(_local_pdf_for_paper(paper), paper_id=paper_id)
    parsed = _parse_references_from_text(text)
    refs = []
    for idx, raw in enumerate(parsed, start=1):
        raw = _trim_reference_entry(raw)
        if _looks_like_reference_fragment(raw):
            continue
        title = _guess_ref_title(raw)
        if not title or _looks_like_bad_ref_title(title):
            continue
        match = _match_registered_paper(title, raw, public_papers)
        refs.append({
            "ref_id": f"ref-{_slugify(paper_id)}-{len(refs) + 1:03d}",
            "paper_id": paper_id,
            "index": len(refs) + 1,
            "raw": raw,
            "title": title,
            "registered_paper_id": match.get("id", "") if match else "",
            "registered_title": match.get("title", "") if match else "",
            "created_by": actor,
            "created_at": _now(),
        })
    with LOCK:
        _write_store_refs(paper_id, refs)
    return {"refs": refs, "count": len(refs)}


def _looks_like_bad_ref_title(title: str) -> bool:
    value = str(title or "").strip()
    if not value:
        return True
    if re.fullmatch(r"\d+", value):
        return True
    if re.fullmatch(r"(?:pp\.?\s*)?\d+\s*[-–]\s*\d+", value, re.I):
        return True
    if re.fullmatch(r"10\.\S+", value, re.I):
        return True
    if re.search(r"\bdoi\b|https?\s*:\s*//|www\.", value, re.I):
        return True
    if re.fullmatch(r"(?:19|20)\d{2}[a-z]?", value):
        return True
    if re.match(r"^(?:doi|URL|https?://|//)\b", value, re.I):
        return True
    if re.match(r"^(?:IEEE|ACM|Springer|PMLR),?\s+(?:19|20)\d{2}\b", value, re.I):
        return True
    if re.fullmatch(r"(?:The\s+)?(?:VLSI\s+journal|Journal|Proceedings|Conference|Transactions|Lecture Notes|Science|Nature)", value, re.I):
        return True
    if re.match(r"^(?:Proceedings of|Conference on|International Conference|IEEE(?:/CVF)?|ACM|Springer|PMLR|Journal of|Transactions on)\b", value, re.I):
        return True
    if re.match(r"^(?:ISBN|ISSN)\b", value, re.I):
        return True
    return False


def _write_store_refs(paper_id: str, refs: list[dict]) -> None:
    paper = _paper_by_id(paper_id)
    refs_path = _paper_store_file(paper or {}, "refs") if paper else None
    if not refs_path:
        return
    refs_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(refs_path, refs)


def _figures_json_path(paper: dict) -> Path | None:
    return _paper_store_file(paper or {}, "figures")


def _figures_dir(paper: dict) -> Path | None:
    path = _paper_store_file(paper or {}, "figures_dir")
    if path:
        return path
    root = str((paper or {}).get("_store_root") or "").strip()
    return Path(root) / "figures" if root else None


def _paper_root(paper: dict) -> Path | None:
    root = str((paper or {}).get("_store_root") or "").strip()
    return Path(root).resolve(strict=False) if root else None


def _asset_version_query(item: dict[str, Any]) -> str:
    version = str((item or {}).get("updated_at") or (item or {}).get("created_at") or "").strip()
    return f"&v={quote(version)}" if version else ""


def _figure_public(figure: dict, paper_id: str) -> dict:
    figure_id = str(figure.get("figure_id") or "")
    return {
        **figure,
        "image_url": f"/paper/api/asset?id={paper_id}&kind=figure&figure_id={figure_id}{_asset_version_query(figure)}" if figure_id else "",
        "generated": [
            {
                **item,
                "image_url": f"/paper/api/asset?id={paper_id}&kind=generated_figure&figure_id={figure_id}&generated_id={item.get('generated_id')}{_asset_version_query(item)}"
                if item.get("image_rel_path")
                else "",
            }
            for item in (figure.get("generated") or [])
            if isinstance(item, dict)
        ],
    }


def _load_figures(paper: dict) -> list[dict]:
    path = _figures_json_path(paper)
    data = _load_json(path, []) if path else []
    return data if isinstance(data, list) else []


def _write_figures(paper: dict, figures: list[dict]) -> None:
    path = _figures_json_path(paper)
    if not path:
        raise ValueError("paper has no figure store")
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, figures)
    _notify_paper_projection(
        paper.get("id"),
        fields=["figures"],
    )


FIGURE_NUMBER_RE = r"[0-9]+(?:\.[0-9]+)*[A-Za-z]?"


def _float_attr(node: ET.Element, name: str, default: float = 0.0) -> float:
    try:
        return float(node.attrib.get(name) or default)
    except Exception:
        return default


def _element_words_text(node: ET.Element) -> str:
    words = []
    for word in node.findall(".//{*}word"):
        text = "".join(word.itertext()).strip()
        if text:
            words.append(text)
    return re.sub(r"\s+", " ", " ".join(words)).strip()


def _line_words(node: ET.Element) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for word in node.findall(".//{*}word"):
        text = "".join(word.itertext()).strip()
        if not text:
            continue
        words.append({
            "text": text,
            "x_min": _float_attr(word, "xMin"),
            "x_max": _float_attr(word, "xMax"),
            "y_min": _float_attr(word, "yMin"),
            "y_max": _float_attr(word, "yMax"),
        })
    return words


def _sanitize_xml_1_0_text(text: str) -> str:
    if not text:
        return ""
    return "".join(
        char
        for char in text
        if char in "\t\n\r" or "\x20" <= char <= "\ud7ff" or "\ue000" <= char <= "\ufffd"
    )


def _caption_number_from_text(text: str) -> str:
    match = re.match(rf"^\s*(?i:(?:Fig\.?|F\s*igure))\s*({FIGURE_NUMBER_RE})\s*(?:(?:[:.\-|]|\u2013|\u2014)\s*|(?=[A-Z][A-Za-z0-9]))", str(text or ""))
    return str(match.group(1) or "").strip() if match else ""


def _caption_number_from_words(words: list[dict[str, Any]], index: int) -> str:
    if index >= len(words):
        return ""
    number_word = str(words[index].get("text") or "").strip()
    match = re.match(rf"^({FIGURE_NUMBER_RE})(?P<suffix>[:.\-|]|\u2013|\u2014)?", number_word)
    if not match:
        return ""
    suffix = str(match.group("suffix") or "")
    if suffix:
        return str(match.group(1) or "").strip()
    next_word = str(words[index + 1].get("text") or "").strip() if index + 1 < len(words) else ""
    if re.match(r"^[A-Z][A-Za-z0-9]", next_word):
        return str(match.group(1) or "").strip()
    if next_word in {"|", "-", "\u2013", "\u2014", ":"}:
        return str(match.group(1) or "").strip()
    return ""


def _caption_segments_for_line(line: ET.Element) -> list[dict[str, Any]]:
    words = _line_words(line)
    starts: list[tuple[int, str]] = []
    for index, word in enumerate(words[:-1]):
        text = str(word.get("text") or "").strip()
        if re.fullmatch(r"(?i:Fig\.?|Figure)", text):
            number_index = index + 1
        elif text.upper() == "F" and index + 2 < len(words) and str(words[index + 1].get("text") or "").strip().upper() == "IGURE":
            number_index = index + 2
        else:
            continue
        number = _caption_number_from_words(words, number_index)
        if number:
            starts.append((index, number))
    if not starts:
        return []
    if starts[0][0] != 0 and not any(number.count(".") >= 2 for _, number in starts):
        return []
    segments: list[dict[str, Any]] = []
    for pos, (start_index, number) in enumerate(starts):
        end_index = starts[pos + 1][0] if pos + 1 < len(starts) else len(words)
        selected = words[start_index:end_index]
        if not selected:
            continue
        segments.append({
            "number": number,
            "text": re.sub(r"\s+", " ", " ".join(str(word.get("text") or "") for word in selected)).strip(),
            "inline": start_index > 0,
            "inline_after_caption": start_index > 0 and starts[0][0] == 0,
            "x_min": min(float(word.get("x_min") or 0) for word in selected),
            "x_max": max(float(word.get("x_max") or 0) for word in selected),
            "y_min": min(float(word.get("y_min") or 0) for word in selected),
            "y_max": max(float(word.get("y_max") or 0) for word in selected),
        })
    return segments


@contextmanager
def _figure_update(paper: dict):
    path = _figures_json_path(paper)
    if path is None:
        raise ValueError("Paper figure artifact location is missing")
    with LOCK, _paper_job_store_lock(path):
        figures = _load_figures(paper)
        yield figures
        _write_figures(paper, figures)


def _figure_number_sort_key(value: Any) -> tuple[int, str]:
    text = str(value or "").strip()
    match = re.match(r"^\s*([0-9]+(?:\.[0-9]+)*)([A-Za-z]?)\s*$", text)
    if not match:
        return (10**9, text)
    parts = tuple(f"{int(part):06d}" for part in str(match.group(1) or "0").split("."))
    return (int(str(match.group(1) or "0").split(".")[0]), ".".join(parts) + str(match.group(2) or "").lower())


def _horizontal_overlap_ratio(a: dict[str, Any], b: dict[str, Any]) -> float:
    a0 = float(a.get("x_min") or 0)
    a1 = float(a.get("x_max") or 0)
    b0 = float(b.get("x_min") or 0)
    b1 = float(b.get("x_max") or 0)
    width = min(max(0.0, a1 - a0), max(0.0, b1 - b0))
    if width <= 0:
        return 0.0
    overlap = max(0.0, min(a1, b1) - max(a0, b0))
    return overlap / width if width else 0.0


def _clamp_nearby_caption_columns(captions: list[dict[str, Any]]) -> None:
    by_page: dict[int, list[dict[str, Any]]] = {}
    for caption in captions:
        by_page.setdefault(int(caption.get("page") or 0), []).append(caption)
    for page_captions in by_page.values():
        for caption in page_captions:
            page_width = max(1.0, float(caption.get("page_width") or 0))
            cap_x0 = float(caption.get("x_min") or 0)
            cap_x1 = float(caption.get("x_max") or page_width)
            cap_y0 = float(caption.get("y_min") or 0)
            cap_y1 = float(caption.get("y_max") or 0)
            right_neighbors = [
                other
                for other in page_captions
                if other is not caption
                and float(other.get("x_min") or 0) - cap_x0 >= page_width * 0.08
                and float(other.get("y_max") or 0) >= cap_y0 - 16.0
                and float(other.get("y_min") or 0) <= cap_y1 + 16.0
            ]
            if not right_neighbors:
                continue
            neighbor_x0 = min(float(other.get("x_min") or page_width) for other in right_neighbors)
            if neighbor_x0 - cap_x0 >= page_width * 0.08:
                boundary = neighbor_x0 - 2.0
                if cap_x1 > page_width * 0.50:
                    caption["x_max"] = min(cap_x1, neighbor_x0 - 6.0)
                    tail_match = re.match(r"^(.*?\.)\s+([a-z][A-Za-z0-9\- ]{1,80}\.)$", str(caption.get("caption") or "").strip())
                    if tail_match:
                        caption["caption"] = tail_match.group(1).strip()
                        for other in right_neighbors:
                            if abs(float(other.get("x_min") or 0) - neighbor_x0) <= 4.0:
                                other["caption"] = f"{str(other.get('caption') or '').rstrip()} {tail_match.group(2).strip()}".strip()
                                break
                caption["crop_x_max"] = min(float(caption.get("crop_x_max") or page_width), boundary)
                for other in right_neighbors:
                    if abs(float(other.get("x_min") or 0) - neighbor_x0) <= 4.0:
                        other["crop_x_min"] = max(float(other.get("crop_x_min") or 0), boundary)
        for caption in page_captions:
            crop_x_min = caption.get("crop_x_min")
            crop_x_max = caption.get("crop_x_max")
            if crop_x_min is None and crop_x_max is None:
                continue
            cap_x0 = float(caption.get("x_min") or 0)
            cap_x1 = float(caption.get("x_max") or page_width)
            cap_y0 = float(caption.get("y_min") or 0)
            cap_y1 = float(caption.get("y_max") or 0)
            row_reaches_right_column = any(
                float(other.get("x_min") or 0) >= page_width * 0.45
                and float(other.get("y_max") or 0) >= cap_y0 - 16.0
                and float(other.get("y_min") or 0) <= cap_y1 + 16.0
                for other in page_captions
            )
            caption["panel_x_min"] = float(crop_x_min) if crop_x_min is not None else max(0.0, cap_x0 - 18.0)
            if crop_x_max is not None:
                caption["panel_x_max"] = float(crop_x_max)
            elif row_reaches_right_column:
                caption["panel_x_max"] = max(0.0, page_width - 30.0)
            else:
                caption["panel_x_max"] = page_width * 0.5 - 4.0
        isscc_grid_page = any(
            str(caption.get("number") or "").count(".") >= 2
            and (caption.get("panel_x_min") is not None or caption.get("panel_x_max") is not None)
            for caption in page_captions
        )
        if isscc_grid_page:
            mid = page_width * 0.5
            for caption in page_captions:
                if caption.get("panel_x_min") is not None or caption.get("panel_x_max") is not None:
                    continue
                if str(caption.get("number") or "").count(".") < 2:
                    continue
                cap_x0 = float(caption.get("x_min") or 0)
                cap_x1 = float(caption.get("x_max") or page_width)
                if cap_x0 < mid and cap_x1 < page_width * 0.80:
                    caption["panel_x_min"] = max(0.0, cap_x0 - 18.0)
                    caption["panel_x_max"] = mid + 2.0
                elif cap_x0 >= mid:
                    caption["panel_x_min"] = mid + 2.0
                    caption["panel_x_max"] = page_width - 30.0


def _prepare_figure_caption_blocks(captions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    y_ordered = sorted(
        (dict(item) for item in captions),
        key=lambda item: (
            int(item.get("page") or 0),
            float(item.get("y_min") or 0),
            float(item.get("x_min") or 0),
        ),
    )
    previous_by_page: dict[int, list[dict[str, Any]]] = {}
    for caption in y_ordered:
        page = int(caption.get("page") or 0)
        cap_y = float(caption.get("y_min") or 0)
        top = 36.0
        for previous in previous_by_page.get(page, []):
            prev_y = float(previous.get("y_max") or 0)
            if prev_y >= cap_y:
                continue
            if _horizontal_overlap_ratio(caption, previous) < 0.25:
                continue
            top = max(top, prev_y + 8.0)
        caption["crop_y_min"] = top
        previous_by_page.setdefault(page, []).append(caption)
    _clamp_nearby_caption_columns(y_ordered)
    return sorted(
        y_ordered,
        key=lambda item: (
            _figure_number_sort_key(item.get("number")),
            int(item.get("page") or 0),
            float(item.get("y_min") or 0),
            float(item.get("x_min") or 0),
        ),
    )


def _caption_line_group(lines: list[ET.Element], start_index: int, segment: dict[str, Any] | None = None) -> tuple[str, float, float, float, float]:
    first = lines[start_index]
    if segment:
        texts = [str(segment.get("text") or "").strip()]
        x_min = float(segment.get("x_min") or _float_attr(first, "xMin"))
        x_max = float(segment.get("x_max") or _float_attr(first, "xMax"))
        y_min = float(segment.get("y_min") or _float_attr(first, "yMin"))
        y_max = float(segment.get("y_max") or _float_attr(first, "yMax"))
        start_x = x_min
        start_y = y_min
    else:
        texts = [_element_words_text(first)]
        x_min = _float_attr(first, "xMin")
        x_max = _float_attr(first, "xMax")
        y_min = _float_attr(first, "yMin")
        y_max = _float_attr(first, "yMax")
        start_x = x_min
        start_y = y_min
    for line in lines[start_index + 1:]:
        text = _element_words_text(line)
        if not text:
            continue
        if _caption_segments_for_line(line):
            break
        line_y = _float_attr(line, "yMin")
        if line_y - start_y > 96.0:
            break
        if abs(_float_attr(line, "xMin") - start_x) > 36.0:
            continue
        if texts[-1].rstrip().endswith(".") and re.match(r"^[a-z]", text):
            break
        texts.append(text)
        x_min = min(x_min, _float_attr(line, "xMin"))
        x_max = max(x_max, _float_attr(line, "xMax"))
        y_min = min(y_min, _float_attr(line, "yMin"))
        y_max = max(y_max, _float_attr(line, "yMax"))
    return re.sub(r"\s+", " ", " ".join(texts)).strip(), x_min, x_max, y_min, y_max


def _extract_figure_caption_blocks(pdf_path: Path) -> list[dict[str, Any]]:
    result = process_runtime.run_process(
        [_poppler_command("pdftotext"), "-bbox-layout", "-enc", "UTF-8", str(pdf_path), "-"],
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=360,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "pdftotext bbox failed").strip()[:500])
    try:
        root = ET.fromstring(_sanitize_xml_1_0_text(result.stdout))
    except Exception as exc:
        raise RuntimeError(f"pdftotext bbox parse failed: {exc}") from exc

    captions: list[dict[str, Any]] = []
    for page_index, page in enumerate(root.findall(".//{*}page"), start=1):
        page_width = _float_attr(page, "width")
        page_height = _float_attr(page, "height")
        page_lines: list[dict[str, Any]] = []
        page_captions: list[dict[str, Any]] = []
        for block in page.findall(".//{*}block"):
            lines = block.findall(".//{*}line")
            for line_index, line in enumerate(lines):
                line_text = _element_words_text(line)
                if line_text:
                    page_lines.append({
                        "text": line_text,
                        "x_min": _float_attr(line, "xMin"),
                        "x_max": _float_attr(line, "xMax"),
                        "y_min": _float_attr(line, "yMin"),
                        "y_max": _float_attr(line, "yMax"),
                    })
                for segment in _caption_segments_for_line(line):
                    figure_number = str(segment.get("number") or "").strip()
                    if not figure_number:
                        continue
                    text, x_min, x_max, y_min, y_max = _caption_line_group(lines, line_index, segment)
                    page_captions.append({
                        "page": page_index,
                        "page_width": page_width,
                        "page_height": page_height,
                        "number": figure_number,
                        "caption": text[:1200],
                        "x_min": x_min,
                        "x_max": x_max or page_width,
                        "y_min": y_min,
                        "y_max": y_max,
                        "_marker_text": str(segment.get("text") or "").strip(),
                        "_inline_caption": bool(segment.get("inline")),
                        "_inline_after_caption": bool(segment.get("inline_after_caption")),
                    })
        anchored_captions = [caption for caption in page_captions if not caption.get("_inline_caption")]
        page_captions = [
            caption
            for caption in page_captions
            if not caption.get("_inline_caption")
            or (
                caption.get("_inline_after_caption")
                or (
                    str(caption.get("number") or "").count(".") >= 2
                    and any(
                        abs(float(anchor.get("y_min") or 0) - float(caption.get("y_min") or 0)) <= 20.0
                        for anchor in anchored_captions
                    )
                )
            )
        ]
        best_by_number: dict[str, dict[str, Any]] = {}
        for caption in page_captions:
            marker = str(caption.pop("_marker_text", "") or "").strip()
            caption.pop("_inline_caption", None)
            caption.pop("_inline_after_caption", None)
            marker_tail = re.sub(r"^(?:Fig\.?|Figure)\s*[0-9]+(?:\.[0-9]+)*[A-Za-z]?\s*[:.]?\s*", "", marker, flags=re.I)
            score = (
                1 if len(marker_tail.split()) >= 2 else 0,
                1 if re.search(r"(?:[0-9]+[A-Za-z]?)\s*:\s*\S", marker) else 0,
                1 if re.match(r"^Figure\b", marker, flags=re.I) else 0,
                len(marker_tail.split()),
                len(marker),
            )
            key = str(caption.get("number") or "").lower()
            current = best_by_number.get(key)
            if current is None or score > current["score"]:
                best_by_number[key] = {"score": score, "caption": caption}
        page_captions = []
        for item in best_by_number.values():
            caption = item["caption"]
            caption["_caption_score"] = item["score"]
            page_captions.append(caption)
        for caption in page_captions:
            caption["page_lines"] = page_lines
            captions.append(caption)
    header_pages: dict[str, set[int]] = {}
    page_samples: dict[int, dict[str, Any]] = {}
    for caption in captions:
        page_samples.setdefault(int(caption.get("page") or 0), caption)
    for page, caption in page_samples.items():
        page_width = max(1.0, float(caption.get("page_width") or 0))
        page_height = max(1.0, float(caption.get("page_height") or 0))
        for line in caption.get("page_lines") or []:
            text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
            line_width = float(line.get("x_max") or 0) - float(line.get("x_min") or 0)
            if (
                float(line.get("y_max") or 0) <= page_height * 0.10
                and line_width >= page_width * 0.15
                and 3 <= len(re.findall(r"[A-Za-z]+", text)) <= 20
                and not _looks_like_figure_internal_text(text)
            ):
                header_pages.setdefault(text.casefold(), set()).add(page)
    repeated_headers = {text for text, pages in header_pages.items() if len(pages) >= 2}
    if repeated_headers:
        for caption in captions:
            for line in caption.get("page_lines") or []:
                text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip().casefold()
                if text in repeated_headers:
                    line["is_running_header"] = True
    return captions


def _render_pdf_page_png(pdf_path: Path, page: int, output_prefix: Path, dpi: int = 160) -> Path:
    result = process_runtime.run_process(
        [
            _poppler_command("pdftoppm"),
            "-png",
            "-r",
            str(dpi),
            "-f",
            str(page),
            "-l",
            str(page),
            "-singlefile",
            str(pdf_path),
            str(output_prefix),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=270,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "pdftoppm failed").strip()[:500])
    path = output_prefix.with_suffix(".png")
    if not path.is_file():
        raise RuntimeError("pdftoppm did not write page image")
    return path


def _pdfimages_pages(pdf_path: Path) -> list[int]:
    result = process_runtime.run_process(
        [_poppler_command("pdfimages"), "-list", str(pdf_path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=270,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "pdfimages list failed").strip()[:500])
    pages: list[int] = []
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if not parts or not parts[0].isdigit():
            continue
        try:
            pages.append(int(parts[0]))
        except Exception:
            pages.append(0)
    return pages


def _extract_embedded_pdf_images(
    pdf_path: Path,
    extracted_dir: Path,
    paper_root: Path,
    paper_id: str,
    actor: str,
    *,
    limit: int = 40,
) -> list[dict]:
    prefix = extracted_dir / "_embedded"
    for old in extracted_dir.glob("_embedded-*"):
        try:
            old.unlink()
        except Exception:
            pass
    pages = _pdfimages_pages(pdf_path)
    result = process_runtime.run_process(
        [_poppler_command("pdfimages"), "-png", str(pdf_path), str(prefix)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=360,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "pdfimages failed").strip()[:500])
    images = sorted(path for path in extracted_dir.glob("_embedded-*") if path.is_file())
    figures: list[dict] = []
    seen_content: set[str] = set()
    for source in images:
        if len(figures) >= limit:
            break
        try:
            with Image.open(source) as image:
                width, height = image.size
        except Exception as exc:
            raise RuntimeError(f"pdfimages produced unreadable image: {source.name}") from exc
        size = source.stat().st_size
        if width < 160 or height < 100 or size < 8_000:
            continue
        content_key = _figure_pixel_digest(source)
        if content_key in seen_content:
            continue
        seen_content.add(content_key)
        index = len(figures) + 1
        figure_id = f"fig-{index:03d}"
        target = extracted_dir / f"{figure_id}.png"
        # pdfimages can produce PBM/PPM for masks despite -png. Store real PNG.
        with Image.open(source) as image:
            image.save(target, format="PNG")
        rel_path = target.relative_to(paper_root).as_posix()
        source_index = int(source.stem.rsplit("-", 1)[-1])
        page = pages[source_index] if source_index < len(pages) else 0
        figures.append({
            "figure_id": figure_id,
            "paper_id": paper_id,
            "index": index,
            "label": f"Figure {index}",
            "caption": "",
            "page": page,
            "width": width,
            "height": height,
            "size_bytes": target.stat().st_size,
            "extraction_mode": "embedded_pdf_image",
            "image_rel_path": rel_path,
            "created_by": actor,
            "created_at": _now(),
            "updated_at": _now(),
        })
    for leftover in extracted_dir.glob("_embedded-*"):
        try:
            leftover.unlink()
        except Exception:
            pass
    return figures


def _nonwhite_row_bounds(
    image: Image.Image,
    *,
    threshold: int = 245,
    gap_limit_cap: int | None = None,
    gap_limit_floor: int | None = None,
) -> tuple[int, int] | None:
    gray = image.convert("L")
    width, height = gray.size
    pixels = gray.load()
    # At low DPI, the two vertical frame edges can each be one pixel wide.
    # Counting them keeps the chart's title, ticks and body in one region.
    min_count = max(2, int(width * 0.003))
    rows: list[int] = []
    for y in range(height):
        count = 0
        for x in range(width):
            if pixels[x, y] < threshold:
                count += 1
                if count >= min_count:
                    rows.append(y)
                    break
    if not rows:
        return None
    bottom = rows[-1]
    gap_limit = max(18, int(height * 0.035))
    if gap_limit_floor is not None:
        gap_limit = max(gap_limit, max(1, int(gap_limit_floor)))
    if gap_limit_cap is not None:
        gap_limit = min(gap_limit, max(1, int(gap_limit_cap)))
    min_height = max(36, int(height * 0.10))
    previous = bottom
    top = rows[0]
    for row in reversed(rows[:-1]):
        if previous - row > gap_limit and bottom - previous >= min_height:
            top = previous
            break
        previous = row
    return max(0, top), min(height - 1, bottom)


def _nonwhite_col_bounds(image: Image.Image, y_min: int, y_max: int, *, threshold: int = 245) -> tuple[int, int] | None:
    gray = image.convert("L")
    width, height = gray.size
    pixels = gray.load()
    y0 = max(0, min(height - 1, y_min))
    y1 = max(y0, min(height - 1, y_max))
    min_count = max(3, int((y1 - y0 + 1) * 0.003))
    cols: list[int] = []
    for x in range(width):
        count = 0
        for y in range(y0, y1 + 1):
            if pixels[x, y] < threshold:
                count += 1
                if count >= min_count:
                    cols.append(x)
                    break
    if not cols:
        return None
    return cols[0], cols[-1]


def _is_nature_style_full_width_caption(caption: dict[str, Any]) -> bool:
    page_width = max(1.0, float(caption.get("page_width") or 0))
    cap_x0 = float(caption.get("x_min") or 0)
    cap_x1 = float(caption.get("x_max") or page_width)
    cap_width = max(0.0, cap_x1 - cap_x0)
    figure_number = str(caption.get("number") or "").strip()
    caption_text = str(caption.get("caption") or "").strip()
    return (
        bool(re.fullmatch(r"\d+", figure_number))
        and bool(re.match(r"^Fig\.\s*\d+\s*\|", caption_text, flags=re.I))
        and cap_x0 <= page_width * 0.14
        and cap_width >= page_width * 0.35
    )


def _page_has_two_text_columns(caption: dict[str, Any]) -> bool:
    page_width = max(1.0, float(caption.get("page_width") or 0))
    mid = page_width * 0.5
    left_lines = 0
    right_lines = 0
    for line in caption.get("page_lines") or []:
        if not isinstance(line, dict):
            continue
        text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
        if len(text) < 35:
            continue
        if re.match(r"^(Fig\.|Figure|Table|Extended Data Fig\.)\s+", text, flags=re.I):
            continue
        line_x0 = float(line.get("x_min") or 0)
        line_x1 = float(line.get("x_max") or 0)
        if line_x0 < mid * 0.85 and line_x1 <= mid + 36.0:
            left_lines += 1
        elif line_x0 >= mid * 0.92:
            right_lines += 1
    return left_lines >= 5 and right_lines >= 5


def _caption_crop_region(caption: dict[str, Any]) -> tuple[float, float, float, float]:
    page_width = max(1.0, float(caption.get("page_width") or 0))
    page_height = max(1.0, float(caption.get("page_height") or 0))
    cap_x0 = float(caption.get("x_min") or 0)
    cap_x1 = float(caption.get("x_max") or page_width)
    cap_y0 = float(caption.get("y_min") or 0)
    cap_width = max(0.0, cap_x1 - cap_x0)
    likely_full_width_main_figure = _is_nature_style_full_width_caption(caption)
    mid = page_width * 0.5
    center = (cap_x0 + cap_x1) / 2.0
    panel_x_min = caption.get("panel_x_min")
    panel_x_max = caption.get("panel_x_max")
    crosses_midline = (
        cap_x0 <= page_width * 0.15
        and cap_x1 >= page_width * 0.55
        and caption.get("crop_x_max") is None
    )
    if panel_x_min is not None or panel_x_max is not None:
        x0 = float(panel_x_min) if panel_x_min is not None else cap_x0 - 18.0
        x1 = float(panel_x_max) if panel_x_max is not None else cap_x1 + 18.0
    elif cap_width >= page_width * 0.50 or likely_full_width_main_figure or crosses_midline:
        x0, x1 = 36.0, page_width - 36.0
    elif abs(center - mid) <= 36.0 and cap_width >= page_width * 0.12:
        x0, x1 = 36.0, page_width - 36.0
    elif _page_has_two_text_columns(caption):
        if abs(center - mid) <= 36.0 and cap_width >= page_width * 0.30:
            x0, x1 = 36.0, page_width - 36.0
        elif center < mid:
            x0, x1 = 36.0, mid - 4.0
        else:
            x0, x1 = mid + 4.0, page_width - 36.0
    else:
        # A short caption does not bound a single-column figure's content.
        # Let pixel bounds trim the page region, retaining chart labels.
        x0, x1 = 36.0, page_width - 36.0
    crop_x_min = caption.get("crop_x_min")
    crop_x_max = caption.get("crop_x_max")
    if crop_x_min is not None:
        x0 = max(x0, float(crop_x_min))
    if crop_x_max is not None:
        x1 = min(x1, float(crop_x_max))
    bottom_guard = 4.0
    for line in caption.get("page_lines") or []:
        line_x0 = float(line.get("x_min") or 0)
        line_x1 = float(line.get("x_max") or 0)
        line_y0 = float(line.get("y_min") or 0)
        line_y1 = float(line.get("y_max") or 0)
        text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
        if (
            line_y0 < cap_y0
            and line_y1 > cap_y0 - 4.0
            and line_x1 >= x0
            and line_x0 <= x1
            and not re.match(r"^(?:Fig\.?|Figure)\s+", text, flags=re.I)
        ):
            bottom_guard = 0.25
            break
    return (
        max(0.0, x0),
        max(0.0, min(page_height, float(caption.get("crop_y_min") or 36.0))),
        min(page_width, x1),
        max(0.0, cap_y0 - bottom_guard),
    )


def _right_body_text_stats(caption: dict[str, Any], y_min: float, y_max: float) -> tuple[int, float | None]:
    page_width = max(1.0, float(caption.get("page_width") or 0))
    buckets: dict[int, list[float]] = {}
    for line in caption.get("page_lines") or []:
        if not isinstance(line, dict):
            continue
        text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
        if len(text) < 35:
            continue
        if re.match(r"^(Fig\.|Figure|Extended Data Fig\.)\s+", text, flags=re.I):
            continue
        line_x0 = float(line.get("x_min") or 0)
        line_x1 = float(line.get("x_max") or 0)
        line_y0 = float(line.get("y_min") or 0)
        line_y1 = float(line.get("y_max") or 0)
        if line_x0 < page_width * 0.45 or line_x1 < page_width * 0.70:
            continue
        if line_y0 < y_min or line_y1 > y_max:
            continue
        bucket = int(round(line_x0 / 8.0) * 8)
        buckets.setdefault(bucket, []).append(line_x0)
    if not buckets:
        return 0, None
    _, values = max(buckets.items(), key=lambda item: len(item[1]))
    return len(values), min(values)


def _body_text_column_stats(
    caption: dict[str, Any],
    y_min: float,
    y_max: float,
    x_min: float,
    x_max: float,
) -> tuple[int, float | None]:
    buckets: dict[int, list[float]] = {}
    for line in caption.get("page_lines") or []:
        if not isinstance(line, dict):
            continue
        text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
        if len(text) < 20 and not re.search(r"[A-Za-z]{3,}[.!?]$", text):
            continue
        if re.match(r"^(Fig\.|Figure|Extended Data Fig\.)\s+", text, flags=re.I):
            continue
        if _looks_like_figure_internal_text(text):
            continue
        line_x0 = float(line.get("x_min") or 0)
        line_x1 = float(line.get("x_max") or 0)
        if line_x1 < x_min or line_x0 > x_max:
            continue
        line_y0 = float(line.get("y_min") or 0)
        line_y1 = float(line.get("y_max") or 0)
        if line_y0 < y_min or line_y1 > y_max:
            continue
        bucket = int(round(line_x0 / 8.0) * 8)
        buckets.setdefault(bucket, []).append(line_y1)
    if not buckets:
        return 0, None
    _, values = max(buckets.items(), key=lambda item: len(item[1]))
    return len(values), max(values)


def _body_text_lines_for_bounds(
    caption: dict[str, Any],
    y_min: float,
    y_max: float,
    x_min: float,
    x_max: float,
) -> list[dict[str, Any]]:
    buckets: dict[int, list[dict[str, Any]]] = {}
    candidates: list[dict[str, Any]] = []
    eligible_lines: list[dict[str, Any]] = []
    region_width = max(1.0, x_max - x_min)
    for line in caption.get("page_lines") or []:
        if not isinstance(line, dict):
            continue
        text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
        structural_heading = bool(
            re.match(r"^[A-Z]\.(?:\d+\.)?\s+\S", text)
            or re.match(r"^Full Results\b", text, flags=re.I)
            or re.fullmatch(r"\(?see\s+Figure\s+\d+[A-Za-z]?\)?\.?", text, flags=re.I)
        )
        if len(text) < 20 and not re.search(r"[A-Za-z]{3,}[.!?]$", text) and not structural_heading:
            continue
        if re.match(r"^(Fig\.|Figure|Extended Data Fig\.)\s+", text, flags=re.I):
            continue
        if _looks_like_figure_internal_text(text):
            continue
        line_x0 = float(line.get("x_min") or 0)
        line_x1 = float(line.get("x_max") or 0)
        if line_x1 < x_min or line_x0 > x_max:
            continue
        line_y0 = float(line.get("y_min") or 0)
        line_y1 = float(line.get("y_max") or 0)
        if line_y1 < y_min or line_y1 > y_max:
            continue
        eligible_lines.append(line)
        bucket = int(round(line_x0 / 8.0) * 8)
        buckets.setdefault(bucket, []).append(line)
        if line_x1 - line_x0 >= region_width * 0.04 and line_y1 - line_y0 <= 18.0:
            candidates.append(line)
    body_lines: list[dict[str, Any]] = []
    for lines in buckets.values():
        if len(lines) >= 5:
            body_lines.extend(lines)

    groups: list[list[dict[str, Any]]] = []
    for line in sorted(candidates, key=lambda item: (float(item.get("y_min") or 0), float(item.get("x_min") or 0))):
        line_x0 = float(line.get("x_min") or 0)
        line_x1 = float(line.get("x_max") or 0)
        line_y0 = float(line.get("y_min") or 0)
        best_group: list[dict[str, Any]] | None = None
        best_gap = 99.0
        for group in groups:
            previous = group[-1]
            previous_y0 = float(previous.get("y_min") or 0)
            previous_y1 = float(previous.get("y_max") or 0)
            start_gap = line_y0 - previous_y0
            vertical_gap = line_y0 - previous_y1
            if start_gap < 2.0 or start_gap > 18.0 or vertical_gap > 8.0:
                continue
            previous_x0 = float(previous.get("x_min") or 0)
            previous_x1 = float(previous.get("x_max") or 0)
            overlap = max(0.0, min(line_x1, previous_x1) - max(line_x0, previous_x0))
            min_width = max(1.0, min(line_x1 - line_x0, previous_x1 - previous_x0))
            if overlap / min_width < 0.35:
                continue
            if vertical_gap < best_gap:
                best_group = group
                best_gap = vertical_gap
        if best_group is None:
            groups.append([line])
        else:
            best_group.append(line)
    for group in groups:
        wide_line_count = sum(
            1
            for line in group
            if float(line.get("x_max") or 0) - float(line.get("x_min") or 0) >= region_width * 0.32
        )
        texts = [re.sub(r"\s+", " ", str(line.get("text") or "")).strip() for line in group]
        two_line_prose = len(group) == 2 and wide_line_count == 2 and any(text.endswith((".", "!", "?")) for text in texts)
        figure_number = re.escape(str(caption.get("number") or "").strip())
        current_figure_leadin = bool(
            figure_number
            and texts
            and re.match(rf"^(?:In\s+)?Figure\s+{figure_number}\b", texts[0], flags=re.I)
        )
        if (len(group) >= 3 and wide_line_count >= 2) or two_line_prose or current_figure_leadin:
            body_lines.extend(group)

    page_height = max(1.0, float(caption.get("page_height") or 0))
    first_page_cutoff = min(y_max - 60.0, page_height * 0.33)
    header_pattern = re.compile(
        r"\b(?:Proceedings|Conference|Symposium|USENIX|MICRO|ISCA|ASPLOS|SOSP|NeurIPS|ICML|ICLR|IEEE|ACM)\b",
        flags=re.I,
    )
    appendix_headings = [
        line
        for line in eligible_lines
        if re.match(r"^[A-Z]\.(?:\d+\.)?\s+\S", re.sub(r"\s+", " ", str(line.get("text") or "")).strip())
    ]
    for line in eligible_lines:
        line_x0 = float(line.get("x_min") or 0)
        line_x1 = float(line.get("x_max") or 0)
        line_y1 = float(line.get("y_max") or 0)
        text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
        line_width = line_x1 - line_x0
        upper_region_line = line_y1 <= y_min + max(1.0, y_max - y_min) * 0.55
        prose_fragment = (
            upper_region_line
            and line_width >= region_width * 0.35
            and len(re.findall(r"[A-Za-z]+", text)) >= 4
            and text.endswith((".", "!", "?"))
        )
        section_heading = (
            len(text.split()) <= 8
            and bool(re.search(r"\b(?:Results?|Analysis|Experiments?|Evaluation|Discussion|Conclusion|Methodology|Background)\b", text, flags=re.I))
        )
        figure_reference_fragment = bool(re.fullmatch(r"\(?see\s+Figure\s+\d+[A-Za-z]?\)?\.?", text, flags=re.I))
        appendix_heading = bool(re.match(r"^[A-Z]\.(?:\d+\.)?\s+\S", text))
        full_results_heading = bool(re.match(r"^Full Results\b", text, flags=re.I))
        appendix_intro_line = any(
            0.0 <= float(line.get("y_min") or 0) - float(heading.get("y_max") or 0) <= 36.0
            for heading in appendix_headings
        )
        first_page_title_line = (
            int(caption.get("page") or 0) == 1
            and line_y1 <= first_page_cutoff
            and line_width >= region_width * 0.15
        )
        running_header_line = line_y1 <= page_height * 0.10 and (
            bool(header_pattern.search(text))
            or bool(re.search(r"\bet\s+al\.\s*$", text, flags=re.I))
            or bool(line.get("is_running_header"))
        )
        if (
            first_page_title_line
            or running_header_line
            or prose_fragment
            or section_heading
            or figure_reference_fragment
            or appendix_heading
            or full_results_heading
            or appendix_intro_line
        ):
            body_lines.append(line)

    unique_lines: list[dict[str, Any]] = []
    seen: set[int] = set()
    for line in body_lines:
        identity = id(line)
        if identity in seen:
            continue
        seen.add(identity)
        unique_lines.append(line)
    return unique_lines


def _table_boundary_above_figure(
    caption: dict[str, Any],
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
) -> float | None:
    lines = sorted(
        (
            line
            for line in (caption.get("page_lines") or [])
            if isinstance(line, dict)
            and float(line.get("y_max") or 0) >= y_min
            and float(line.get("y_min") or 0) <= y_max
            and float(line.get("x_max") or 0) >= x_min
            and float(line.get("x_min") or 0) <= x_max
        ),
        key=lambda line: (float(line.get("y_min") or 0), float(line.get("x_min") or 0)),
    )
    for index, line in enumerate(lines):
        text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
        if not re.match(r"^Table\s+[A-Za-z0-9]", text, flags=re.I):
            continue
        boundary = float(line.get("y_max") or 0)
        if text.endswith((".", "!", "?")):
            return boundary
        for following in lines[index + 1 :]:
            following_y0 = float(following.get("y_min") or 0)
            following_y1 = float(following.get("y_max") or 0)
            if following_y0 - boundary > 14.0:
                break
            boundary = max(boundary, following_y1)
            following_text = re.sub(r"\s+", " ", str(following.get("text") or "")).strip()
            if following_text.endswith((".", "!", "?")):
                break
        return boundary
    return None


def _looks_like_figure_internal_text(text: str) -> bool:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return False
    if re.match(r"^\([a-z]\)\s+", text, flags=re.I):
        return True
    if re.match(r"^(?:\d+\s+)?(?:def|class|while|for|if|elif|else|return)\b", text):
        return True
    if re.search(r"(?:[A-Za-z_]\w*\.)+[A-Za-z_]\w*\s*=", text):
        return True
    code_keywords = (
        " return ",
        " if ",
        " elif ",
        " else:",
        " for ",
        " while ",
        " def ",
        "client.",
        "print(",
    )
    code_symbols = (
        " = ",
        " == ",
        "{",
        "}",
        "[",
        "]",
    )
    padded = f" {text} "
    if any(marker in padded for marker in code_keywords) and any(marker in padded for marker in code_symbols):
        return True
    label_markers = (
        "ML API",
        "API output",
        "App decision",
        "Ground-truth",
        "Code snippet",
        "Label output",
        "Multi-Choice",
        "Multi-Select",
        "True-False",
    )
    return any(marker in text for marker in label_markers)


def _figure_internal_line_count(caption: dict[str, Any], y_min: float, y_max: float) -> int:
    count = 0
    for line in caption.get("page_lines") or []:
        if not isinstance(line, dict):
            continue
        text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
        if not _looks_like_figure_internal_text(text):
            continue
        line_y0 = float(line.get("y_min") or 0)
        line_y1 = float(line.get("y_max") or 0)
        if line_y1 < y_min or line_y0 > y_max:
            continue
        count += 1
    return count


def _has_merged_subfigure_labels(caption: dict[str, Any], y_min: float, y_max: float) -> bool:
    for line in caption.get("page_lines") or []:
        if not isinstance(line, dict):
            continue
        line_y0 = float(line.get("y_min") or 0)
        line_y1 = float(line.get("y_max") or 0)
        if line_y1 < y_min or line_y0 > y_max:
            continue
        text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
        if len(re.findall(r"\([a-z]\)", text, flags=re.I)) >= 2:
            return True
    return False


def _candidate_with_body_text_masked_for_bounds(
    candidate: Image.Image,
    caption: dict[str, Any],
    x0: float,
    y0: float,
    y1: float,
    scale_x: float,
    scale_y: float,
    body_lines: list[dict[str, Any]] | None = None,
) -> Image.Image:
    if body_lines is None:
        body_lines = _body_text_lines_for_bounds(caption, y0, y1, x0, x0 + (candidate.width / scale_x))
    if not body_lines:
        return candidate
    masked = candidate.copy()
    draw = ImageDraw.Draw(masked)
    for line in body_lines:
        lx0 = int((float(line.get("x_min") or 0) - x0) * scale_x) - 4
        lx1 = int((float(line.get("x_max") or 0) - x0) * scale_x) + 4
        ly0 = int((float(line.get("y_min") or 0) - y0) * scale_y) - 4
        ly1 = int((float(line.get("y_max") or 0) - y0) * scale_y) + 4
        left = max(0, min(masked.width, lx0))
        right = max(0, min(masked.width, lx1))
        top = max(0, min(masked.height, ly0))
        bottom = max(0, min(masked.height, ly1))
        if right <= left or bottom <= top:
            continue
        draw.rectangle(
            (left, top, right, bottom),
            fill="white",
        )
    return masked


def _crop_figure_below_heading(page_image: Image.Image, caption: dict[str, Any]) -> Image.Image | None:
    page_width = max(1.0, float(caption.get("page_width") or 0))
    page_height = max(1.0, float(caption.get("page_height") or 0))
    scale_x, scale_y = page_image.width / page_width, page_image.height / page_height
    lines = caption.get("page_lines") or []
    if not lines:
        return None
    masked = page_image.convert("L")
    draw = ImageDraw.Draw(masked)
    for line in lines:
        draw.rectangle((
            max(0, (float(line.get("x_min") or 0) - 2) * scale_x),
            max(0, (float(line.get("y_min") or 0) - 2) * scale_y),
            min(page_image.width, (float(line.get("x_max") or 0) + 2) * scale_x),
            min(page_image.height, (float(line.get("y_max") or 0) + 2) * scale_y),
        ), fill=255)
    graphics = masked.point(lambda value: 255 if value < 235 else 0)
    caption_top = max(0, min(page_image.height, int(float(caption.get("y_min") or 0) * scale_y)))
    caption_bottom = max(caption_top, min(page_image.height, int(float(caption.get("y_max") or 0) * scale_y)))
    following = sorted(
        (float(line.get("y_min") or 0), _caption_number_from_text(str(line.get("text") or "")))
        for line in lines
        if float(line.get("y_min") or 0) > float(caption.get("y_max") or 0)
        and _caption_number_from_text(str(line.get("text") or ""))
    )
    caption_number = str(caption.get("number") or _caption_number_from_text(str(caption.get("caption") or "")))
    matching_lower_caption = bool(following and caption_number and following[0][1] == caption_number)
    preceding = graphics.crop((0, 0, page_image.width, caption_top)).getbbox()
    bottom = min(page_image.height, int(following[0][0] * scale_y)) if following else page_image.height
    content_top = caption_top if matching_lower_caption else caption_bottom
    bounds = graphics.crop((0, content_top, page_image.width, bottom)).getbbox()
    if not bounds or bounds[2] - bounds[0] < 100 * scale_x or bounds[3] - bounds[1] < 25 * scale_y:
        return None
    if preceding and not matching_lower_caption:
        # A heading can sit inside a figure's outer frame. Require both sides
        # to continue through the heading into the chart; an unrelated rule or
        # an earlier figure above the caption must not select this branch.
        tolerance = max(2, math.ceil(3 * scale_x))
        side_bottom = min(bottom, caption_bottom + math.ceil(15 * scale_y))
        frame = (
            caption_top - preceding[1] <= max(24 * scale_y, 2 * (caption_bottom - caption_top))
            and abs(preceding[0] - bounds[0]) <= tolerance
            and abs(preceding[2] - bounds[2]) <= tolerance
        )
        if frame:
            for edge in (preceding[0], preceding[2] - 1):
                rows = graphics.crop((
                    max(0, edge - tolerance), preceding[1],
                    min(page_image.width, edge + tolerance + 1), side_bottom,
                )).getprojection()[1]
                if not rows or sum(rows) < 0.9 * len(rows):
                    frame = False
                    break
        if not frame:
            return None
        content_top = preceding[1]
        bounds = graphics.crop((0, content_top, page_image.width, bottom)).getbbox()
    graphic_bounds = (
        bounds[0] / scale_x, (content_top + bounds[1]) / scale_y,
        bounds[2] / scale_x, (content_top + bounds[3]) / scale_y,
    )
    label_heights = sorted(
        max(1.0, float(line.get("y_max") or 0) - float(line.get("y_min") or 0))
        for line in lines
        if float(line.get("y_min") or 0) >= graphic_bounds[1]
        and float(line.get("y_max") or 0) <= graphic_bounds[3]
        and float(line.get("x_max") or 0) >= graphic_bounds[0]
        and float(line.get("x_min") or 0) <= graphic_bounds[2]
    )
    label_height = label_heights[len(label_heights) // 2] if label_heights else 12.0
    # This branch has identified a chart below its own heading. Preserve that
    # heading as well as the plot; text masking is only for finding graphics.
    included_bounds = [
        min(graphic_bounds[0], float(caption.get("x_min") or 0)),
        min(graphic_bounds[1], caption_top / scale_y),
        max(graphic_bounds[2], float(caption.get("x_max") or 0)),
        graphic_bounds[3],
    ]
    for line in lines:
        text = str(line.get("text") or "").strip()
        line_bounds = tuple(float(line.get(key) or 0) for key in ("x_min", "y_min", "x_max", "y_max"))
        if not text or _caption_number_from_text(text) or line_bounds[1] < content_top / scale_y:
            continue
        if (line_bounds[1] if matching_lower_caption else line_bounds[3]) > bottom / scale_y:
            continue
        height = max(1.0, line_bounds[3] - line_bounds[1])
        horizontal_gap = max(graphic_bounds[0] - line_bounds[2], line_bounds[0] - graphic_bounds[2], 0)
        vertical_gap = max(graphic_bounds[1] - line_bounds[3], line_bounds[1] - graphic_bounds[3], 0)
        # A large following section heading must not enlarge its own admission
        # distance and pull unrelated prose into the figure crop.
        if horizontal_gap > max(48.0, 4 * height) or vertical_gap > max(36.0, 3 * min(height, label_height)):
            continue
        prose = len(text) > 160 or (len(text.split()) >= 8 and re.search(r"[.!?。！？]$", text))
        if prose and (horizontal_gap > 0 or vertical_gap > 0):
            continue
        included_bounds[0] = min(included_bounds[0], line_bounds[0])
        included_bounds[1] = min(included_bounds[1], line_bounds[1])
        included_bounds[2] = max(included_bounds[2], line_bounds[2])
        included_bounds[3] = max(included_bounds[3], line_bounds[3])
    # Footer annotations can contain full sentences without a "Note:" prefix.
    # A compact block aligned within the plot belongs with its nearby labels;
    # full-width body prose and separated sections must not extend the crop.
    note_left = None
    note_continues = False
    footer_lines = sorted(lines, key=lambda item: float(item.get("y_min") or 0))
    uninterrupted = True
    for index, line in enumerate(footer_lines):
        text = str(line.get("text") or "").strip()
        x0, y0, x1, y1 = (float(line.get(key) or 0) for key in ("x_min", "y_min", "x_max", "y_max"))
        if y0 < included_bounds[3] or y1 > bottom / scale_y:
            continue
        height = max(1.0, y1 - y0)
        explicit_note = re.match(r"^(?:Caption|Notes?|Disclaimer)\s*[:：]", text, flags=re.I)
        continuation = note_continues and note_left is not None and abs(x0 - note_left) <= height
        inset_tolerance = max(12.0, 2 * min(height, label_height))
        compact_note = (
            len(text) >= 20 and re.search(r"[.!?。！？]$", text)
            and included_bounds[0] - inset_tolerance <= x0
            and x1 <= graphic_bounds[2] + inset_tolerance
            and (note_left is None or abs(x0 - note_left) <= height)
        )
        if not (explicit_note or continuation or compact_note):
            note_continues = False
            if text:
                uninterrupted = False
            continue
        gap_limit = max(24.0, 2 * min(height, label_height)) if explicit_note or compact_note else 1.5 * height
        if compact_note and uninterrupted and note_left is None and index + 1 < len(footer_lines):
            following_line = footer_lines[index + 1]
            following_text = str(following_line.get("text") or "").strip()
            nx0, ny0, nx1, ny1 = (float(following_line.get(key) or 0)
                                  for key in ("x_min", "y_min", "x_max", "y_max"))
            # A pair of compact sentences aligned with the plot provides more
            # evidence than a lone sentence separated from its axis labels.
            # Bound the extra whitespace by the plot size, and never cross a
            # section heading or absorb full-width body text.
            aligned_pair = (
                len(following_text) >= 20 and re.search(r"[.!?。！？]$", following_text)
                and not _caption_number_from_text(following_text)
                and abs(nx0 - x0) <= height
                and x0 >= graphic_bounds[0] - inset_tolerance
                and nx1 <= graphic_bounds[2] + inset_tolerance
                and 0 <= ny0 - y1 <= max(12.0, 1.5 * height)
                and 0 < ny1 - ny0 <= 1.5 * height
                and ny1 <= bottom / scale_y
            )
            if aligned_pair:
                gap_limit = max(gap_limit, min(48.0, 0.2 * (graphic_bounds[3] - graphic_bounds[1])))
        if y0 - included_bounds[3] > gap_limit or x1 < graphic_bounds[0] or x0 > graphic_bounds[2]:
            note_continues = False
            uninterrupted = False
            continue
        included_bounds[0] = min(included_bounds[0], x0)
        included_bounds[2] = max(included_bounds[2], x1)
        included_bounds[3] = y1
        note_left = x0
        note_continues = not re.search(r"[.!?。！？]$", text)
    padding_x, padding_y = int(20 * scale_x), int(20 * scale_y)
    if matching_lower_caption:
        bottom = min(page_image.height, max(bottom, math.ceil(included_bounds[3] * scale_y)))
    crop_bottom = min(bottom, int(included_bounds[3] * scale_y) + padding_y)
    for line in lines:
        line_top = float(line.get("y_min") or 0) * scale_y
        line_bottom = float(line.get("y_max") or 0) * scale_y
        if line_top > included_bounds[3] * scale_y and line_top < crop_bottom < line_bottom:
            # Padding may include whitespace, never half of the next paragraph.
            crop_bottom = max(int(included_bounds[3] * scale_y), int(line_top) - 2)
    return page_image.crop((
        max(0, int(included_bounds[0] * scale_x) - padding_x),
        max(min(content_top, caption_top), int(included_bounds[1] * scale_y) - padding_y),
        min(page_image.width, int(included_bounds[2] * scale_x) + padding_x),
        crop_bottom,
    ))


def _crop_figure_above_caption(page_image: Image.Image, caption: dict[str, Any]) -> Image.Image | None:
    below_heading = _crop_figure_below_heading(page_image, caption)
    if below_heading is not None:
        return below_heading
    page_width = max(1.0, float(caption.get("page_width") or 0))
    page_height = max(1.0, float(caption.get("page_height") or 0))
    x0, y0, x1, y1 = _caption_crop_region(caption)
    if x1 <= x0 or y1 <= y0:
        return None
    scale_x = page_image.width / page_width
    scale_y = page_image.height / page_height
    left = max(0, int(x0 * scale_x))
    top = max(0, int(y0 * scale_y))
    right = min(page_image.width, int(x1 * scale_x))
    bottom = min(page_image.height, int(y1 * scale_y))
    if right - left < 80 or bottom - top < 60:
        return None
    candidate = page_image.crop((left, top, right, bottom))
    body_lines = _body_text_lines_for_bounds(caption, y0, y1, x0, x1)
    cap_x0 = float(caption.get("x_min") or 0)
    cap_x1 = float(caption.get("x_max") or page_width)
    left_body_lines = [
        line
        for line in body_lines
        if float(line.get("x_max") or 0) <= cap_x0 - 8.0
    ]
    if (
        len(left_body_lines) >= 5
        and cap_x0 - x0 >= (x1 - x0) * 0.25
        and cap_x1 - cap_x0 <= (x1 - x0) * 0.45
    ):
        x0 = max(x0, cap_x0 - 18.0)
        left = max(0, int(x0 * scale_x))
        candidate = page_image.crop((left, top, right, bottom))
        body_lines = _body_text_lines_for_bounds(caption, y0, y1, x0, x1)
    bounds_candidate = _candidate_with_body_text_masked_for_bounds(
        candidate, caption, x0, y0, y1, scale_x, scale_y, body_lines
    )
    nature_full_width_caption = _is_nature_style_full_width_caption(caption)
    two_text_columns = _page_has_two_text_columns(caption)
    body_text_column_count, body_text_column_y_max = _body_text_column_stats(caption, y0, y1, x0, x1)
    figure_internal_line_count = _figure_internal_line_count(caption, y0, y1)
    merged_subfigure_labels = _has_merged_subfigure_labels(caption, y0, y1)
    row_bounds = _nonwhite_row_bounds(
        bounds_candidate,
        gap_limit_cap=30 if nature_full_width_caption or body_text_column_count >= 5 else None,
        gap_limit_floor=int(candidate.height * 0.22)
        if (
            figure_internal_line_count >= 2
            or merged_subfigure_labels
            or (body_text_column_count < 5 and float(caption.get("crop_y_min") or 36.0) > 40.0)
        )
        else None,
    )
    if not row_bounds:
        return None
    row_top, row_bottom = row_bounds
    row_top_floor = 0
    minimum_remaining_height = 40 if candidate.width >= 400 else 80
    table_boundary = _table_boundary_above_figure(caption, x0, x1, y0, y1)
    if table_boundary is not None:
        table_bottom = int((table_boundary + 4.0 - y0) * scale_y)
        if row_bottom - table_bottom >= minimum_remaining_height:
            row_top = max(row_top, table_bottom)
            row_top_floor = max(row_top_floor, table_bottom)
    if body_lines:
        body_boundary = max(float(line.get("y_max") or 0) for line in body_lines)
        body_bottom = int((body_boundary + 4.0 - y0) * scale_y)
        if row_bottom - body_bottom >= minimum_remaining_height:
            row_top = max(row_top, body_bottom)
            row_top_floor = max(row_top_floor, body_bottom)
    if body_text_column_count >= 5 and body_text_column_y_max is not None:
        text_bottom = int((body_text_column_y_max + 4.0 - y0) * scale_y)
        if row_bottom - text_bottom >= minimum_remaining_height:
            row_top = max(row_top, text_bottom)
            row_top_floor = max(row_top_floor, text_bottom)
    if (nature_full_width_caption or body_text_column_count >= 5) and not two_text_columns:
        crop_pdf_y0 = y0 + (row_top / scale_y)
        crop_pdf_y1 = y0 + (row_bottom / scale_y)
        body_text_count, body_text_x_min = _right_body_text_stats(caption, crop_pdf_y0, crop_pdf_y1)
        if body_text_count >= 5:
            cap_x0 = float(caption.get("x_min") or 0)
            cap_x1 = float(caption.get("x_max") or page_width)
            if body_text_x_min is not None:
                cap_x1 = min(cap_x1, body_text_x_min - 26.0)
            narrow_left = max(0, int(max(0.0, cap_x0 - 18.0) * scale_x))
            narrow_right = min(page_image.width, int(min(page_width, cap_x1 + 18.0) * scale_x))
            if narrow_right - narrow_left >= 80:
                candidate = page_image.crop((narrow_left, top, narrow_right, bottom))
                narrow_x0 = narrow_left / scale_x
                body_lines = _body_text_lines_for_bounds(
                    caption, y0, y1, narrow_x0, narrow_x0 + (candidate.width / scale_x)
                )
                bounds_candidate = _candidate_with_body_text_masked_for_bounds(
                    candidate, caption, narrow_x0, y0, y1, scale_x, scale_y, body_lines
                )
                row_bounds = _nonwhite_row_bounds(bounds_candidate)
                if not row_bounds:
                    return None
                row_top, row_bottom = row_bounds
    # A numbered chart title can be separated from its plot by more whitespace
    # than the pixel grouping permits. Include a nearby matching title while
    # respecting the table/prose boundary already established above.
    figure_number = re.escape(str(caption.get("number") or "").strip())
    if figure_number:
        plot_top = y0 + row_top / scale_y
        for line in caption.get("page_lines") or []:
            text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
            if not re.match(rf"^(?:Fig\.?|Figure)\s+{figure_number}(?:[.:]|\s|$)", text, flags=re.I):
                continue
            heading_top = float(line.get("y_min") or 0)
            heading_bottom = float(line.get("y_max") or 0)
            heading_height = heading_bottom - heading_top
            if (
                heading_height > 0
                and 0 <= plot_top - heading_bottom <= 2 * heading_height
                and (heading_top - y0) * scale_y >= row_top_floor
                and float(line.get("x_max") or 0) >= x0
                and float(line.get("x_min") or 0) <= x1
            ):
                row_top = min(row_top, int((heading_top - y0) * scale_y))
    col_bounds = _nonwhite_col_bounds(bounds_candidate, row_top, row_bottom)
    if not col_bounds:
        return None
    col_left, col_right = col_bounds
    pad = max(8, int(min(candidate.size) * 0.015))
    crop_box = (
        max(0, col_left - pad),
        max(0, row_top_floor, row_top - pad),
        min(candidate.width, col_right + pad),
        min(candidate.height, row_bottom + pad),
    )
    crop_width = crop_box[2] - crop_box[0]
    crop_height = crop_box[3] - crop_box[1]
    wide_short_figure = crop_width >= 300 and crop_height >= 40 and crop_width / max(1, crop_height) >= 4.0
    if crop_width < 120 or (crop_height < 80 and not wide_short_figure):
        return None
    return candidate.crop(crop_box)


def _dedupe_figures_by_label(figures: list[dict]) -> list[dict]:
    labels = {str(figure.get("label") or "").strip().lower() for figure in figures}
    best_by_label: dict[str, dict] = {}
    dropped: list[dict] = []
    for figure in figures:
        label = str(figure.get("label") or "").strip().lower()
        suffix_match = re.match(r"^(figure\s+)([0-9]+)[a-z]$", label, flags=re.I)
        if suffix_match and f"{suffix_match.group(1)}{suffix_match.group(2)}".lower() in labels:
            dropped.append(figure)
            continue
        key = label or str(figure.get("figure_id") or "")
        current = best_by_label.get(key)
        if not current:
            best_by_label[key] = figure
            continue
        figure_caption_score = tuple(figure.get("_caption_score") or ())
        current_caption_score = tuple(current.get("_caption_score") or ())
        figure_score = (
            figure_caption_score,
            int(figure.get("width") or 0) * int(figure.get("height") or 0),
            int(figure.get("size_bytes") or 0),
        )
        current_score = (
            current_caption_score,
            int(current.get("width") or 0) * int(current.get("height") or 0),
            int(current.get("size_bytes") or 0),
        )
        if figure_score > current_score:
            dropped.append(current)
            best_by_label[key] = figure
        else:
            dropped.append(figure)
    for figure in dropped:
        path = _safe_file_path(str(figure.get("_path_for_cleanup") or ""))
        if path and path.is_file():
            try:
                path.unlink()
            except Exception:
                pass
    kept = sorted(
        best_by_label.values(),
        key=lambda item: (
            _figure_number_sort_key(re.sub(r"^\s*figure\s+", "", str(item.get("label") or ""), flags=re.I)),
            int(item.get("page") or 0),
            int(item.get("index") or 0),
        ),
    )
    for index, figure in enumerate(kept, start=1):
        figure["index"] = index
        figure["figure_id"] = f"fig-{index:03d}"
        figure.pop("_path_for_cleanup", None)
        figure.pop("_caption_score", None)
    return kept


def _figure_pixel_digest(path: Path) -> str:
    with Image.open(path) as image, image.convert("RGBA") as pixels:
        digest = hashlib.sha256(str(pixels.size).encode("ascii"))
        digest.update(pixels.tobytes())
        return digest.hexdigest()


def _extracted_figure_key(figure: dict, paper_root: Path) -> tuple[str, ...] | None:
    """Only verified source identity, never a display position, carries annotations."""
    mode = str(figure.get("extraction_mode") or "")
    if mode == "caption_region_crop":
        label = re.sub(r"\s+", " ", str(figure.get("label") or "")).strip().casefold()
        caption = re.sub(r"\s+", " ", str(figure.get("caption") or "")).strip()
        return (mode, label, caption) if label and caption else None
    if mode == "embedded_pdf_image":
        path = _paper_bundle_path(paper_root, str(figure.get("image_rel_path") or ""))
        if path and path.is_file():
            try:
                return (mode, _figure_pixel_digest(path))
            except (OSError, ValueError):
                pass
    return None


def _reconcile_extracted_figures(figures: list[dict], existing: list[dict], paper_root: Path) -> list[dict]:
    by_key: dict[tuple[str, ...], list[dict]] = {}
    used_ids = {str(item.get("figure_id") or "") for item in existing}
    for old in existing:
        key = _extracted_figure_key(old, paper_root)
        if key is not None:
            by_key.setdefault(key, []).append(old)
    reconciled = []
    for index, candidate in enumerate(figures, start=1):
        key = _extracted_figure_key(candidate, paper_root)
        matches = by_key.pop(key, []) if key is not None else []
        old = matches[0] if len(matches) == 1 and matches[0].get("figure_id") else {}
        if old:
            figure_id = old["figure_id"]
        else:
            # Local opaque identity survives reordering and is never allocated
            # from a recycled display position after an empty extraction.
            figure_id = f"fig-{secrets.token_hex(12)}"
            while figure_id in used_ids:
                figure_id = f"fig-{secrets.token_hex(12)}"
        used_ids.add(figure_id)
        merged = {
            "explanation": "", "generated": [], **old, **candidate,
            "figure_id": figure_id, "index": index,
            "created_by": old.get("created_by", candidate["created_by"]),
            "created_at": old.get("created_at", candidate["created_at"]),
        }
        if old and candidate.get("extraction_mode") == "embedded_pdf_image":
            merged["caption"] = str(old.get("caption") or "")
        reconciled.append(merged)
    return reconciled


def _render_extracted_figures(pdf_path: Path, extracted_dir: Path, paper_root: Path, paper_id: str, actor: str) -> list[dict]:
    """Build an unreferenced generation; the caller owns its commit and cleanup."""
    captions = _prepare_figure_caption_blocks(_extract_figure_caption_blocks(pdf_path))
    figures: list[dict] = []
    page_images: dict[int, Image.Image] = {}
    page_image_paths: list[Path] = []
    try:
        for caption in captions:
            page = int(caption.get("page") or 0)
            if page <= 0:
                continue
            if page not in page_images:
                page_path = _render_pdf_page_png(pdf_path, page, extracted_dir / f"_page-{page:03d}")
                page_image_paths.append(page_path)
                with Image.open(page_path) as page_image:
                    page_images[page] = page_image.convert("RGB")
            cropped = _crop_figure_above_caption(page_images[page], caption)
            if cropped is None:
                continue
            index = len(figures) + 1
            figure_id = f"fig-{index:03d}"
            path = extracted_dir / f"{figure_id}.png"
            with cropped:
                cropped.save(path)
                width, height = cropped.size
            size = path.stat().st_size
            wide_short_figure = width >= 300 and height >= 40 and width / max(1, height) >= 4.0
            minimum_size = 2_000 if wide_short_figure else 4_000
            if width < 120 or (height < 80 and not wide_short_figure) or size < minimum_size:
                path.unlink()
                continue
            figures.append({
                "figure_id": figure_id, "paper_id": paper_id, "index": index,
                "label": f"Figure {str(caption.get('number') or index).strip()}",
                "caption": str(caption.get("caption") or ""), "page": page,
                "width": width, "height": height, "size_bytes": size,
                "extraction_mode": "caption_region_crop",
                "image_rel_path": path.relative_to(paper_root).as_posix(),
                "_path_for_cleanup": str(path), "_caption_score": caption.get("_caption_score") or (),
                "created_by": actor, "created_at": _now(), "updated_at": _now(),
            })
    finally:
        for image in page_images.values():
            image.close()
        for path in page_image_paths:
            path.unlink(missing_ok=True)
    figures = _dedupe_figures_by_label(figures)
    if not figures:
        figures = _extract_embedded_pdf_images(pdf_path, extracted_dir, paper_root, paper_id, actor)
    return figures


def _read_figure_extraction_metadata(path: Path, *, missing_ok: bool = False) -> list[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if missing_ok:
            return []
        raise
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise ValueError("Paper figure metadata must be a list of objects")
    return payload


def extract_figures(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    if not paper_id:
        raise ValueError("paper_id required")
    paper = _paper_by_id(paper_id)
    if not paper:
        raise KeyError("paper not found")
    source_pdf_reference = str(paper.get("pdf_path") or "")
    pdf_path = _local_pdf_for_paper(paper)
    figures_dir = _figures_dir(paper)
    paper_root = _paper_root(paper)
    metadata_path = _figures_json_path(paper)
    if not figures_dir or not paper_root or not metadata_path:
        raise ValueError("paper has no figure store")
    # Serialize extraction per paper without holding the global metadata lock
    # during PDF rendering. Annotation writers remain free to finish meanwhile.
    with _paper_job_store_lock(figures_dir / "extraction"):
        initial_pdf = pdf_path.stat()
        extracted_root = figures_dir / "extracted"
        generation = extracted_root / f"run-{secrets.token_hex(12)}"
        generation.mkdir(parents=True)
        preserve_generation = False
        warning = ""
        try:
            figures = _render_extracted_figures(pdf_path, generation, paper_root, paper_id, actor)
            with LOCK, _paper_job_store_lock(metadata_path):
                current = _paper_by_id(paper_id)
                if not current or _paper_root(current) != paper_root:
                    raise RuntimeError("Paper changed during figure extraction")
                if str(current.get("pdf_path") or "") != source_pdf_reference:
                    raise RuntimeError("PDF changed during figure extraction")
                final_pdf = pdf_path.stat()
                if (initial_pdf.st_ino, initial_pdf.st_size, initial_pdf.st_mtime_ns) != (
                    final_pdf.st_ino, final_pdf.st_size, final_pdf.st_mtime_ns
                ):
                    raise RuntimeError("PDF changed during figure extraction")
                existing = _read_figure_extraction_metadata(metadata_path, missing_ok=True)
                figures = _reconcile_extracted_figures(figures, existing, paper_root)
                # A write or notification failure can happen after the atomic
                # metadata switch. Never delete files with an uncertain commit.
                preserve_generation = True
                try:
                    _write_figures(paper, figures)
                except Exception as exc:
                    persisted = _read_figure_extraction_metadata(metadata_path)
                    if persisted != figures:
                        prefix = generation.relative_to(paper_root).as_posix() + "/"
                        preserve_generation = any(str(item.get("image_rel_path") or "").startswith(prefix) for item in persisted)
                        raise
                    warning = f"Figures persisted; post-write handling reported: {exc}"
                preserve_generation = bool(figures)
                protected = {str(item.get("image_rel_path") or "") for item in figures}
                protected.update(str(image.get("image_rel_path") or "") for item in figures for image in item.get("generated", []))
                for old in existing:
                    rel = str(old.get("image_rel_path") or "")
                    path = _paper_bundle_path(paper_root, rel) if rel else None
                    if path and rel not in protected and path.is_relative_to(extracted_root.resolve()):
                        try:
                            path.unlink(missing_ok=True)
                            if path.parent != extracted_root:
                                path.parent.rmdir()  # remove only empty old generations
                        except OSError:
                            pass
        finally:
            if not preserve_generation:
                shutil.rmtree(generation, ignore_errors=True)
        result = {"figures": [_figure_public(item, paper_id) for item in figures], "count": len(figures)}
        if warning:
            result["persistence_warning"] = warning
        return result


class FigureExplanationPersistenceError(RuntimeError):
    pass


def explain_figure(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    figure_id = str(payload.get("figure_id") or "").strip()
    if not paper_id or not figure_id:
        raise ValueError("paper_id and figure_id required")
    paper = _paper_by_id(paper_id)
    if not paper:
        raise KeyError("paper not found")
    figures = _load_figures(paper)
    figure = next((item for item in figures if str(item.get("figure_id") or "") == figure_id), None)
    if not figure:
        raise KeyError("figure not found")
    explanation, usage = _generate_figure_explanation(paper, figure)
    if not isinstance(explanation, str) or not explanation.strip():
        raise ValueError("Figure explanation returned no generated content")
    committed_at = _now()
    receipt = {
        "schema": "tfi.paper.figure-explanation.commit.v1", "commit_id": secrets.token_hex(12),
        "paper_id": paper_id, "figure_id": figure_id,
        "content_sha256": hashlib.sha256(explanation.encode("utf-8")).hexdigest(),
        "committed_by": actor, "committed_at": committed_at,
    }
    write_error = None
    try:
        _set_figure_fields(paper_id, figure_id, {
            "explanation": explanation, "explanation_usage": usage,
            "explanation_status": "done", "explanation_error": "",
            "explanation_generation_status": "generated", "explanation_persistence_status": "saved",
            "explanation_commit": receipt,
            "explained_by": actor, "explained_at": committed_at, "updated_at": committed_at,
        })
    except Exception as exc:
        write_error = exc
    try:
        persisted = next((item for item in _load_figures(paper) if item.get("figure_id") == figure_id), {})
    except Exception as exc:
        raise FigureExplanationPersistenceError(f"Figure explanation generated but persistence could not be verified: {exc}") from exc
    if (persisted.get("explanation_commit") != receipt or persisted.get("explanation") != explanation
            or persisted.get("explanation_status") != "done" or persisted.get("explanation_persistence_status") != "saved"):
        raise FigureExplanationPersistenceError(
            f"Figure explanation generated but persistence could not be verified: {write_error or 'commit receipt missing'}"
        ) from write_error
    result = {"figure": _figure_public(persisted, paper_id)}
    if write_error is not None:
        result["persistence_warning"] = f"Explanation persisted; post-write handling reported: {write_error}"
    return result

def start_figure_explanation(payload: dict, actor: str) -> dict:
    with LOCK:
        return _queue_figure_explanation(payload, actor)


def _queue_figure_explanation(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    figure_id = str(payload.get("figure_id") or "").strip()
    if not paper_id or not figure_id:
        raise ValueError("paper_id and figure_id required")
    paper = _paper_by_id(paper_id)
    if not paper:
        raise KeyError("paper not found")
    with _figure_update(paper) as figures:
        figure = next((item for item in figures if str(item.get("figure_id") or "") == figure_id), None)
        if not figure:
            raise KeyError("figure not found")
        if str(figure.get("explanation_status") or "") in {"queued", "running"}:
            return {"figure": _figure_public(figure, paper_id), "status": figure.get("explanation_status") or "queued"}
        figure["explanation_status"] = "queued"
        figure["explanation_generation_status"] = "queued"
        figure["explanation_persistence_status"] = "pending"
        figure["explanation_commit"] = {}
        figure["explanation_error"] = ""
        figure["explanation_invocation"] = {}
        figure["explained_by"] = actor
        figure["explain_requested_at"] = _now()
        figure["updated_at"] = _now()
    launch_error = _start_figure_explain_task(paper, figure_id, actor)
    if launch_error:
        _set_figure_fields(paper_id, figure_id, {
            "explanation_status": "error", "explanation_error": launch_error,
            "updated_at": _now(),
        })
        paper = _paper_by_id(paper_id) or paper
        figure = next((item for item in _load_figures(paper) if str(item.get("figure_id") or "") == figure_id), figure)
    return {"figure": _figure_public(figure, paper_id), "status": figure.get("explanation_status") or "queued"}


def generate_figure_draft(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    figure_id = str(payload.get("figure_id") or "").strip()
    request = str(payload.get("request") or payload.get("message") or "").strip()
    generated_id = str(payload.get("generated_id") or "").strip()
    source_generated_id = str(payload.get("source_generated_id") or "").strip()
    if not paper_id or not figure_id:
        raise ValueError("paper_id and figure_id required")
    if not request:
        raise ValueError("request required")
    paper = _paper_by_id(paper_id)
    if not paper:
        raise KeyError("paper not found")
    figures = _load_figures(paper)
    figure = next((item for item in figures if str(item.get("figure_id") or "") == figure_id), None)
    if not figure:
        raise KeyError("figure not found")
    if not generated_id:
        generated_id = f"gen-{secrets.token_hex(5)}"
    with _figure_update(paper) as current_figures:
        current_figure = next((item for item in current_figures if item.get("figure_id") == figure_id), None)
        if current_figure is None:
            raise KeyError("figure not found")
        generated = current_figure.setdefault("generated", [])
        if not any(item.get("generated_id") == generated_id for item in generated):
            generated.insert(0, {
                "generated_id": generated_id, "status": "queued", "kind": "image",
                "request": request, "created_by": actor, "created_at": _now(),
                "source_generated_id": source_generated_id,
                "extra_image_paths": [str(path) for path in _safe_figure_input_image_paths(paper, payload.get("extra_image_paths"))],
            })
        figure = current_figure
    extra_image_paths = _safe_figure_input_image_paths(paper, payload.get("extra_image_paths"))
    image_rel_path, resident_note, usage = _generate_figure_image(
        paper,
        figure,
        request,
        generated_id,
        source_generated_id=source_generated_id,
        extra_image_paths=extra_image_paths,
    )
    with _figure_update(paper) as figures:
        figure = next((item for item in figures if str(item.get("figure_id") or "") == figure_id), None)
        if not figure:
            raise KeyError("figure not found")
        generated = figure.get("generated") if isinstance(figure.get("generated"), list) else []
        item = {
            "generated_id": generated_id,
            "kind": "image",
            "status": "done",
            "request": request,
            "prompt": resident_note,
            "image_rel_path": image_rel_path,
            "source_generated_id": source_generated_id,
            "extra_image_paths": [str(path) for path in extra_image_paths],
            "usage": usage,
            "created_by": actor,
            "created_at": _now(),
            "updated_at": _now(),
        }
        replaced = False
        for index, existing in enumerate(generated):
            if str(existing.get("generated_id") or "") == str(item.get("generated_id") or ""):
                item["created_at"] = int(existing.get("created_at") or item["created_at"])
                generated[index] = {**existing, **item}
                item = generated[index]
                replaced = True
                break
        if not replaced:
            generated.insert(0, item)
        figure["generated"] = generated[:8]
        figure["updated_at"] = _now()
    return {"figure": _figure_public(figure, paper_id), "generated": item}


def start_figure_draft(payload: dict, actor: str) -> dict:
    with LOCK:
        return _queue_figure_draft(payload, actor)


def _queue_figure_draft(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    figure_id = str(payload.get("figure_id") or "").strip()
    request = str(payload.get("request") or payload.get("message") or "").strip()
    source_generated_id = str(payload.get("source_generated_id") or "").strip()
    if not paper_id or not figure_id:
        raise ValueError("paper_id and figure_id required")
    if not request:
        raise ValueError("request required")
    paper = _paper_by_id(paper_id)
    if not paper:
        raise KeyError("paper not found")
    extra_image_paths = _safe_figure_input_image_paths(paper, payload.get("extra_image_paths"))
    with _figure_update(paper) as figures:
        figure = next((item for item in figures if str(item.get("figure_id") or "") == figure_id), None)
        if not figure:
            raise KeyError("figure not found")
        generated = figure.get("generated") if isinstance(figure.get("generated"), list) else []
        now = _now()
        for existing in generated:
            if (
                isinstance(existing, dict)
                and str(existing.get("request") or "").strip() == request
                and str(existing.get("source_generated_id") or "").strip() == source_generated_id
                and [str(path) for path in extra_image_paths] == list(existing.get("extra_image_paths") or [])
                and str(existing.get("status") or "") in {"queued", "running"}
            ):
                touched = int(existing.get("updated_at") or existing.get("created_at") or 0)
                if touched > 0 and now - touched <= FIGURE_DRAFT_STALE_SECONDS:
                    return {"figure": _figure_public(figure, paper_id), "generated": existing, "status": existing.get("status") or "queued"}
                existing["status"] = "error"
                existing["error"] = "Previous figure generation business task became stale before finishing."
                existing["usage"] = {
                    **(existing.get("usage") if isinstance(existing.get("usage"), dict) else {}),
                    "error": existing["error"],
                    "stale_after_seconds": FIGURE_DRAFT_STALE_SECONDS,
                }
                existing["updated_at"] = now
        generated_id = f"gen-{secrets.token_hex(5)}"
        queued = {
            "generated_id": generated_id,
            "kind": "image",
            "status": "queued",
            "request": request,
            "prompt": "",
            "image_rel_path": "",
            "source_generated_id": source_generated_id,
            "extra_image_paths": [str(path) for path in extra_image_paths],
            "usage": {},
            "created_by": actor,
            "created_at": _now(),
            "updated_at": _now(),
        }
        generated.insert(0, queued)
        figure["generated"] = generated[:8]
        figure["updated_at"] = _now()
    launch_error = _start_figure_draft_task(
        paper,
        figure_id,
        generated_id,
        request,
        actor,
        source_generated_id=source_generated_id,
        extra_image_paths=extra_image_paths,
    )
    if launch_error:
        _set_figure_generated_item(
            paper_id,
            figure_id,
            generated_id,
            {
                "status": "error",
                "error": launch_error,
                "usage": {"error": launch_error},
                "updated_at": _now(),
            },
        )
        paper = _paper_by_id(paper_id) or paper
        figure = next((item for item in _load_figures(paper) if str(item.get("figure_id") or "") == figure_id), figure)
        queued = next((item for item in (figure.get("generated") or []) if str(item.get("generated_id") or "") == generated_id), queued)
    return {"figure": _figure_public(figure, paper_id), "generated": queued, "status": queued.get("status") or "queued"}


def delete_generated_figure(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    figure_id = str(payload.get("figure_id") or "").strip()
    generated_id = str(payload.get("generated_id") or "").strip()
    if not paper_id or not figure_id or not generated_id:
        raise ValueError("paper_id, figure_id and generated_id required")
    paper = _paper_by_id(paper_id)
    if not paper:
        raise KeyError("paper not found")
    paper_root = _paper_root(paper)
    with _figure_update(paper) as figures:
        updated_figure = None
        removed = None
        for figure in figures:
            if str(figure.get("figure_id") or "") != figure_id:
                continue
            generated = figure.get("generated") if isinstance(figure.get("generated"), list) else []
            kept = []
            for item in generated:
                if isinstance(item, dict) and str(item.get("generated_id") or "") == generated_id:
                    removed = item
                    continue
                kept.append(item)
            if removed is None:
                raise KeyError("generated figure not found")
            figure["generated"] = kept
            figure["updated_at"] = _now()
            updated_figure = figure
            break
        if not updated_figure:
            raise KeyError("figure not found")
        rel_path = str((removed or {}).get("image_rel_path") or "").strip()
        if paper_root and rel_path:
            try:
                path = (paper_root / rel_path).resolve(strict=False)
                if paper_root.resolve() in path.parents and path.is_file():
                    path.unlink()
            except Exception:
                pass
    return {"figure": _figure_public(updated_figure, paper_id), "deleted": generated_id}


def _start_figure_explain_task(paper: dict, figure_id: str, actor: str) -> str:
    from management_portal import business_tasks

    paper_id = str(paper.get("id") or "").strip()
    def fail(exc: BaseException) -> None:
        _set_figure_fields(paper_id, figure_id, {
            "explanation_status": "error",
            "explanation_error": str(exc)[:2000],
            "updated_at": _now(),
        })

    try:
        business_tasks.start(
            f"paper-figure-explain:{paper_id}:{figure_id}",
            run_figure_explain_job,
            paper_id,
            figure_id,
            actor,
            on_error=fail,
        )
    except Exception as exc:
        return f"Failed to run figure explanation business task: {exc}"
    return ""


def _recover_figure_tasks(paper: dict) -> None:
    with LOCK:
        _resume_figure_tasks(paper)


def _resume_figure_tasks(paper: dict) -> None:
    from management_portal import business_tasks

    paper_id = paper["id"]
    for figure in _load_figures(paper):
        figure_id = str(figure.get("figure_id") or "")
        if figure.get("explanation_status") in {"queued", "running"} and not business_tasks.is_active(f"paper-figure-explain:{paper_id}:{figure_id}"):
            binding = figure.get("explanation_invocation") or {}
            if (binding.get("resident_id") and binding.get("input_message_id")
                    and binding.get("input_identity_status") != "unconfirmed"):
                _start_figure_explain_task(paper, figure_id, str(figure.get("explained_by") or paper.get("created_by") or ""))
            else:
                _set_figure_fields(paper_id, figure_id, {
                    "explanation_status": "error",
                    "explanation_error": "Figure task interrupted before its Resident input binding was saved. Check the Resident before retrying; no input was resubmitted.",
                })
        for item in figure.get("generated") or []:
            generated_id = str(item.get("generated_id") or "")
            if item.get("status") not in {"queued", "running"} or business_tasks.is_active(f"paper-figure-draft:{generated_id}"):
                continue
            binding = item.get("invocation") or {}
            if (binding.get("resident_id") and binding.get("input_message_id")
                    and binding.get("input_identity_status") != "unconfirmed"):
                _start_figure_draft_task(
                    paper, figure_id, generated_id, str(item.get("request") or ""),
                    str(item.get("created_by") or paper.get("created_by") or ""),
                    source_generated_id=str(item.get("source_generated_id") or ""),
                    extra_image_paths=_safe_figure_input_image_paths(paper, item.get("extra_image_paths")),
                )
            else:
                _set_figure_generated_item(paper_id, figure_id, generated_id, {
                    "status": "error",
                    "error": "Figure task interrupted before its Resident input binding was saved. Check the Resident before retrying; no input was resubmitted.",
                })


def _start_figure_draft_task(
    paper: dict,
    figure_id: str,
    generated_id: str,
    request: str,
    actor: str,
    source_generated_id: str = "",
    extra_image_paths: list[Path] | None = None,
) -> str:
    from management_portal import business_tasks

    paper_id = str(paper.get("id") or "").strip()
    image_values = [str(path) for path in (extra_image_paths or [])]
    def fail(exc: BaseException) -> None:
        _set_figure_generated_item(
            paper_id,
            figure_id,
            generated_id,
            {
                "status": "error",
                "error": str(exc)[:2000],
                "updated_at": _now(),
            },
        )

    try:
        business_tasks.start(
            f"paper-figure-draft:{generated_id}",
            run_figure_draft_job,
            paper_id,
            figure_id,
            generated_id,
            request,
            actor,
            source_generated_id=source_generated_id,
            extra_image_paths=image_values,
            on_error=fail,
        )
    except Exception as exc:
        return f"Failed to run figure draft business task: {exc}"
    return ""


def _set_figure_generated_item(paper_id: str, figure_id: str, generated_id: str, updates: dict) -> dict:
    paper = _paper_by_id(paper_id)
    if not paper:
        raise KeyError("paper not found")
    with _figure_update(paper) as figures:
        updated_figure = None
        updated_item = None
        for figure in figures:
            if str(figure.get("figure_id") or "") != figure_id:
                continue
            generated = figure.get("generated") if isinstance(figure.get("generated"), list) else []
            for item in generated:
                if isinstance(item, dict) and str(item.get("generated_id") or "") == generated_id:
                    item.update(updates)
                    item["updated_at"] = int(item.get("updated_at") or _now())
                    updated_item = item
                    break
            figure["generated"] = generated
            figure["updated_at"] = _now()
            updated_figure = figure
            break
        if not updated_figure or not updated_item:
            raise KeyError("figure draft not found")
    return {"figure": _figure_public(updated_figure, paper_id), "generated": updated_item}


def _set_figure_fields(paper_id: str, figure_id: str, updates: dict) -> dict:
    paper = _paper_by_id(paper_id)
    if not paper:
        raise KeyError("paper not found")
    with _figure_update(paper) as figures:
        updated_figure = None
        for figure in figures:
            if str(figure.get("figure_id") or "") == figure_id:
                figure.update(updates)
                figure["updated_at"] = int(figure.get("updated_at") or _now())
                updated_figure = figure
                break
        if not updated_figure:
            raise KeyError("figure not found")
    return {"figure": _figure_public(updated_figure, paper_id)}


def run_figure_explain_job(paper_id: str, figure_id: str, actor: str) -> dict:
    try:
        _set_figure_fields(paper_id, figure_id, {
            "explanation_status": "running", "explanation_generation_status": "running",
            "explanation_persistence_status": "pending", "explanation_commit": {}, "updated_at": _now(),
        })
        return explain_figure({"paper_id": paper_id, "figure_id": figure_id}, actor)
    except Exception as exc:
        persistence_failure = isinstance(exc, FigureExplanationPersistenceError)
        return _set_figure_fields(paper_id, figure_id, {
            "explanation_status": "error", "explanation_error": str(exc),
            "explanation_generation_status": "generated" if persistence_failure else "error",
            "explanation_persistence_status": "error" if persistence_failure else "not_saved",
            "explanation_commit": {},
            "updated_at": _now(),
        })

def run_figure_draft_job(
    paper_id: str,
    figure_id: str,
    generated_id: str,
    request: str,
    actor: str,
    source_generated_id: str = "",
    extra_image_paths: list[str] | None = None,
) -> dict:
    _set_figure_generated_item(paper_id, figure_id, generated_id, {"status": "running", "updated_at": _now()})
    try:
        return generate_figure_draft(
            {
                "paper_id": paper_id,
                "figure_id": figure_id,
                "generated_id": generated_id,
                "request": request,
                "source_generated_id": source_generated_id,
                "extra_image_paths": extra_image_paths or [],
            },
            actor,
        )
    except Exception as exc:
        return _set_figure_generated_item(
            paper_id,
            figure_id,
            generated_id,
            {
                "status": "error",
                "error": str(exc),
                "usage": {"error": str(exc)},
                "updated_at": _now(),
            },
        )


def _generate_figure_explanation(paper: dict, figure: dict) -> tuple[str, dict]:
    analysis_path = _paper_store_file(paper, "analysis")
    analysis = _read_text_preview(analysis_path, limit=18_000)
    pdf_path = _safe_file_path(str(paper.get("pdf_path") or ""))
    image_path = _figure_image_path(paper, figure)
    if not image_path:
        raise FileNotFoundError("Figure image missing")
    prompt = f"""你是 TFI Portal 的论文 Figure 解读助手。

请查看给定 Figure 图片，并结合论文 metadata、caption、analysis excerpt 和 PDF 路径解释这张图。不要只根据 caption 猜测；优先使用图片中的可见结构、文字、坐标、箭头、模块和图例。必要时可以读取 PDF 文件或 analysis 文件。

Figure image:
[image: {image_path}]

Paper PDF:
[file: {pdf_path or paper.get('pdf_path') or ''}]

Analysis file:
{analysis_path or ''}

本次只负责生成解释，Paper 服务负责校验和正式保存，不要求你直接写入文件或调用提交接口。
生成成功不表示已经正式保存；保存成功与否由 Paper 服务根据真实持久化回执确认。
请只返回一个 JSON 对象，不要代码围栏或前后说明，字段如下：
{{"schema": "{FIGURE_EXPLANATION_RESULT_SCHEMA}", "paper_id": "{paper.get('id') or ''}", "figure_id": "{figure.get('figure_id') or ''}", "status": "generated", "explanation": "中文 Markdown 解读正文", "error": ""}}
只有解释生成成功且有实际内容时才能使用 status=generated；无法生成时使用 status=error，explanation 为空，error 说明真实失败原因。
不要把未要求你执行的正式保存当成生成失败，不要生成或伪造保存回执。
explanation 字段的中文 Markdown 正文包含：

## 图在表达什么
## 读图顺序
## 它支持论文哪个结论
## 需要回看原图或正文确认的点

Paper:
- Title: {paper.get('title') or ''}
- Authors: {paper.get('authors') or ''}
- Venue: {paper.get('venue') or ''} {paper.get('year') or ''}
- Abstract: {paper.get('abstract') or ''}

Figure:
- Label: {figure.get('label') or figure.get('figure_id') or ''}
- Page: {figure.get('page') or ''}
- Size: {figure.get('width') or ''} x {figure.get('height') or ''}
- Caption: {figure.get('caption') or ''}

Analysis excerpt:
{analysis}
"""
    content, usage = _run_figure_resident(prompt, paper, figure, "explain", images=[image_path])
    try:
        generated = json.loads(content)
    except (TypeError, ValueError) as exc:
        raise ValueError("Figure explanation requires a structured generation result, not a submission claim or free text") from exc
    if (not isinstance(generated, dict)
            or not schema_matches(generated.get("schema"), "tfi.paper.figure-explanation.result.v1")
            or generated.get("paper_id") != paper.get("id") or generated.get("figure_id") != figure.get("figure_id")):
        raise ValueError("Figure explanation generation result schema or Paper/Figure identity mismatch")
    if generated.get("status") == "error":
        raise RuntimeError(f"Figure explanation generation failed: {str(generated.get('error') or 'no reason provided')[:2000]}")
    explanation = generated.get("explanation")
    if (generated.get("status") != "generated" or not isinstance(explanation, str) or not explanation.strip()
            or generated.get("error") not in (None, "")):
        raise ValueError("Figure explanation did not return a successful generated artifact")
    return explanation.strip(), usage


def _generated_figure_image_path(paper: dict, figure: dict, generated_id: str) -> Path | None:
    paper_root = _paper_root(paper)
    if not paper_root or not generated_id:
        return None
    for item in figure.get("generated") or []:
        if not isinstance(item, dict) or str(item.get("generated_id") or "") != generated_id:
            continue
        rel_path = str(item.get("image_rel_path") or "").strip()
        if not rel_path:
            return None
        path = paper_root / rel_path
        try:
            resolved = path.resolve(strict=False)
            if not resolved.is_relative_to(paper_root.resolve()):
                return None
            return resolved if resolved.is_file() else None
        except (OSError, RuntimeError, ValueError):
            return None
    return None


def _safe_figure_input_image_paths(paper: dict, raw_paths: object) -> list[Path]:
    paper_root = _paper_root(paper)
    if not paper_root or not isinstance(raw_paths, list):
        return []
    safe_paths: list[Path] = []
    try:
        resolved_root = paper_root.resolve(strict=False)
    except Exception:
        return []
    for raw in raw_paths[:8]:
        path = _safe_file_path(str(raw or ""))
        if not path:
            continue
        try:
            resolved = path.resolve(strict=False)
            resolved.relative_to(resolved_root)
        except Exception:
            continue
        if resolved.is_file():
            safe_paths.append(resolved)
    return safe_paths


def _generate_figure_image(
    paper: dict,
    figure: dict,
    request: str,
    generated_id: str,
    source_generated_id: str = "",
    extra_image_paths: list[Path] | None = None,
) -> tuple[str, str, dict]:
    figures_dir = _figures_dir(paper)
    paper_root = _paper_root(paper)
    source_generated_id = str(source_generated_id or "").strip()
    image_path = _generated_figure_image_path(paper, figure, source_generated_id) if source_generated_id else _figure_image_path(paper, figure)
    source_label = f"generated image {source_generated_id}" if source_generated_id else "original Figure"
    if not figures_dir or not paper_root:
        raise ValueError("paper has no figure store")
    if not image_path:
        raise ValueError("source image missing")
    generated_dir = figures_dir / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    output_path = generated_dir / f"{_slugify(generated_id)}.png"
    analysis_path = _paper_store_file(paper, "analysis")
    analysis = _read_text_preview(analysis_path, limit=8_000)
    extra_image_paths = [path for path in (extra_image_paths or []) if path and path.is_file()]
    extra_images_note = "\n".join(f"- {path}" for path in extra_image_paths) if extra_image_paths else "None"
    prompt = f"""你是 TFI Portal 的论文 Figure 生成助手。

请使用 `$imagegen` 基于输入的原 Figure 生成一张新的 PNG 图片。必须把最终图片文件保存到这个路径：

{output_path}

如果 `$imagegen` 把图片保存到了 `$CODEX_HOME/generated_images/...`，请把最终选择的 PNG 复制或转换到上面的路径。最终路径必须存在，而且必须是 PNG 图片。

输入图片中的第一张是待修改的 {source_label}。后续图片是用户从 Paper 输入框粘贴或在放大视图标注后提供的参考图。请优先参考这些图片中的圈画、箭头、文字说明和视觉修改意图。

Additional reference images:
{extra_images_note}

请保持论文含义，不要编造新的实验数据、坐标值、方法名或结论。用户修改需求优先；如果需求含糊，就生成更清晰、更适合论文展示的版本。

Paper title: {paper.get('title') or ''}
Figure label: {figure.get('label') or figure.get('figure_id') or ''}
Figure caption: {figure.get('caption') or ''}
Existing explanation: {figure.get('explanation') or ''}

Analysis excerpt:
{analysis}

User modification request:
{request}

输出要求：
- 生成或编辑一张实际图片，不要只写 prompt。
- 保存 PNG 到指定路径。
- 回复中只简要说明保存路径和主要改动。
"""
    input_images = [image_path, *extra_image_paths]
    content, usage = _run_figure_resident(prompt, paper, figure, "generate-image", images=input_images, generated_id=generated_id)
    if not output_path.is_file():
        raise RuntimeError(f"paper:figure-generation did not write generated image: {output_path}")
    try:
        with Image.open(output_path) as image:
            image.verify()
    except Exception as exc:
        raise RuntimeError(f"generated image is not a readable image: {output_path}") from exc
    rel_path = output_path.relative_to(paper_root).as_posix()
    usage = {
        **usage,
        "output_image_path": str(output_path),
        "source_image_path": str(image_path),
        "source_generated_id": source_generated_id,
        "extra_image_paths": [str(path) for path in extra_image_paths],
    }
    return rel_path, content.strip(), usage


def _parse_references_from_text(text: str, limit: int = 2000) -> list[str]:
    block = _reference_block_from_text(text or "")
    # An explicit, scored bibliography heading establishes the section even in
    # short conference papers. The heading-free heuristic still requires five.
    minimum_entries = 1 if block else 5
    if not block:
        source = text or ""
        tail_start = int(len(source) * 0.45)
        tail = source[tail_start:]
        for marker_match in re.finditer(r"\n\s*(?:\[1\]|1\.|1\])\s+", tail):
            start = tail_start + marker_match.start()
            probe = source[start : start + 5000]
            if (
                re.search(r"\n\s*(?:\[2\]|2\.|2\])\s+", probe)
                and re.search(r"\n\s*(?:\[3\]|3\.|3\])\s+", probe)
                and re.search(r"\b(?:Preprint|arXiv|Proceedings|Conference|Journal|Nature|Science|Neural Information Processing Systems|doi|https?://)\b", probe, re.I)
            ):
                block = source[start:]
                break
    if not block:
        return []
    block = _cut_reference_block(block)
    lines = _decolumnize_reference_lines(list(block.splitlines()))
    broad_marker = re.compile(r"^(?:\[\d{1,3}\]|\d{1,2}\.|\d{1,3}\])\s+")
    first_marker = next((str(line or "").strip() for line in lines[:40] if broad_marker.match(str(line or "").strip())), "")
    first_marker_idx = next((idx for idx, line in enumerate(lines[:40]) if broad_marker.match(str(line or "").strip())), None)
    first_author_idx = next((idx for idx, line in enumerate(lines[:40]) if _looks_like_unnumbered_ref_start(str(line or "").strip())), None)
    entries = []
    current = []
    if first_marker_idx is None or (first_author_idx is not None and first_author_idx < first_marker_idx):
        fallback = _parse_unnumbered_reference_lines(lines)
        return fallback[:limit] if len(fallback) >= minimum_entries else []
    if first_marker.startswith("["):
        marker = re.compile(r"^\[\d{1,3}\]\s+")
        marker_token = re.compile(r"^\[\d{1,3}\](?:\s|$)")
    elif re.match(r"^\d{1,3}\]", first_marker):
        marker = re.compile(r"^\d{1,3}\]\s+")
        marker_token = re.compile(r"^\d{1,3}\](?:\s|$)")
    else:
        marker = re.compile(r"^\d{1,3}\.\s+")
        marker_token = re.compile(r"^\d{1,3}\.(?:\s|$)")
    # A long bibliography with detached labels is not a short bibliography.
    # Keep its existing unnumbered fallback when too few complete entries parse.
    if sum(bool(marker_token.match(str(line).strip())) for line in lines) >= 5:
        minimum_entries = max(minimum_entries, 5)
    for line in lines:
        line = line.replace("\x0c", " ").strip()
        if not line:
            continue
        if _is_reference_page_noise(line):
            continue
        if re.search(r"\bQuestion:\s+Does the paper\b", line, re.I):
            break
        if marker.match(line) and not _looks_like_numbered_reference_line(line):
            continue
        if marker.match(line) and current:
            entries.append(" ".join(current).strip())
            current = [line]
        elif marker.match(line):
            current = [line]
        elif not current:
            continue
        elif _reference_entry_ended(" ".join(current)) and not _looks_like_reference_continuation(line):
            continue
        else:
            current.append(line)
    if current:
        entries.append(" ".join(current).strip())
    entries = _sort_numbered_reference_entries(entries)
    cleaned = [_trim_reference_entry(item) for item in entries if len(item) > 20]
    cleaned = [item for item in cleaned if len(item) > 20 and not _looks_like_reference_fragment(item)][:limit]
    if len(cleaned) >= minimum_entries:
        return cleaned
    fallback = _parse_unnumbered_reference_lines(lines)
    return fallback[:limit] if len(fallback) >= minimum_entries else []


def _sort_numbered_reference_entries(entries: list[str]) -> list[str]:
    numbered: list[tuple[int, int, str]] = []
    for idx, entry in enumerate(entries):
        match = re.match(r"^\s*(?:\[(\d{1,4})\]|(\d{1,4})[.\]])\s+", str(entry or ""))
        if not match:
            return entries
        numbered.append((int(match.group(1) or match.group(2) or 0), idx, entry))
    if len(numbered) < 3:
        return entries
    ordered = sorted(numbered, key=lambda item: (item[0], item[1]))
    numbers = [item[0] for item in ordered]
    if len(set(numbers)) != len(numbers):
        return entries
    return [item[2] for item in ordered]


def _reference_block_from_text(text: str) -> str:
    source = str(text or "")
    if not source.strip():
        return ""
    line_re = re.compile(r"(?m)^.*$")
    heading_re = re.compile(
        r"(?i)(?:(?:\d+(?:\.\d+)*\.?\s+)?(?:References|Bibliography|R\s*E\s*F\s*E\s*R\s*E\s*N\s*C\s*E\s*S))[:：]?"
    )
    candidates: list[tuple[int, int, str]] = []
    for line_match in line_re.finditer(source):
        heading_line = line_match.group(0)
        if not heading_line.strip():
            continue
        heading_match = None
        for candidate in heading_re.finditer(heading_line):
            prefix = heading_line[: candidate.start()]
            suffix = heading_line[candidate.end():]
            if prefix.strip() and not re.search(r"\s{6,}$", prefix):
                continue
            if suffix.strip() and not re.match(r"\s{6,}", suffix):
                continue
            heading_match = candidate
            break
        if not heading_match:
            continue
        line_end = source.find("\n", line_match.start())
        if line_end < 0:
            continue
        inline_right = ""
        inline_match = re.search(r"\s{3,}((?:\[\d{1,3}\]|\d{1,3}[.\]])\s+\S.*)$", heading_line[heading_match.end():])
        if inline_match:
            inline_right = inline_match.group(1).strip() + "\n"
        after_heading = source[line_end + 1 :]
        blocks_to_score = [inline_right + after_heading]
        if heading_match.start() >= 40 and heading_line[: heading_match.start()].strip():
            right_only = _right_column_block_until_page_break(after_heading)
            if right_only.strip():
                blocks_to_score.append(inline_right + right_only)
        for raw_block in blocks_to_score:
            block = _cut_reference_block(raw_block)
            score = _score_reference_block(block[:24000])
            if score >= 7:
                candidates.append((score, line_match.start() + heading_match.start(), block))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates[0][2]


def _right_column_block_until_page_break(text: str) -> str:
    first_page, sep, rest = str(text or "").partition("\f")
    right_lines: list[str] = []
    for line in first_page.splitlines():
        raw = str(line or "").rstrip()
        if not raw.strip():
            right_lines.append("")
            continue
        split = re.match(r"^(.*?\S)\s{6,}(\S.*)$", raw)
        if split:
            right_lines.append(split.group(2).strip())
        elif re.match(r"^\s{32,}\S", raw):
            right_lines.append(raw.strip())
    joined = "\n".join(right_lines).strip()
    if sep:
        return f"{joined}\n{sep}{rest}" if joined else f"{sep}{rest}"
    return joined


def _cut_reference_block(block: str) -> str:
    section_cut = re.search(
        r"\n\s*(?:(?:[A-Z]|\d+(?:\.\d+)*)\.\s+)?(?:Supplementary\s+Related\s+Work|Appendix|APPENDIX|Acknowledg(?:e)?ments?|Author\s+Contributions?)\b",
        block,
    )
    page_appendix_cut = re.search(
        r"\n\s*\x0c?[^\n]{0,120}\s+\d+\s*\n\s*[A-Z]\s+(?:Detailed|Additional|Ablation|Experimental|Implementation|Proof|Reproducibility|Limitations|Attention|Hyperparameter|More|Benchmark|Dataset|Training|Evaluation)\b[^\n]{0,120}\n",
        block,
    )
    if page_appendix_cut and (not section_cut or page_appendix_cut.start() < section_cut.start()):
        section_cut = page_appendix_cut
    return block[: section_cut.start()] if section_cut else block


def _score_reference_block(block: str) -> int:
    lines = [line.strip() for line in str(block or "").splitlines() if line.strip()]
    if not lines:
        return 0
    numbered = sum(1 for line in lines[:220] if re.match(r"^(?:\[\d{1,3}\]|\d{1,3}[.\]])\s+\S", line))
    unnumbered = sum(1 for line in lines[:220] if _looks_like_unnumbered_ref_start(line) or _looks_like_reference_first_line(line))
    signals = len(re.findall(r"\b(?:arXiv|doi|URL|Proceedings|Conference|Journal|Transactions|Advances|Science|Nature|IEEE|ACM|PMLR|Springer)\b", "\n".join(lines[:220]), re.I))
    first_ref_idx = next(
        (
            idx
            for idx, line in enumerate(lines[:35])
            if re.match(r"^(?:\[\d{1,3}\]|\d{1,3}[.\]])\s+\S", line)
            or _looks_like_unnumbered_ref_start(line)
            or _looks_like_reference_first_line(line)
        ),
        None,
    )
    if first_ref_idx is None:
        return 0
    score = numbered * 7 + unnumbered * 3 + min(signals, 80)
    if first_ref_idx > 10:
        score -= 80
    if re.search(r"\b(?:thank|acknowledg|insightful comments|helpful discussion)\b", "\n".join(lines[:20]), re.I):
        score -= 30
    return score


def _decolumnize_reference_lines(lines: list[str]) -> list[str]:
    left: list[str] = []
    right: list[str] = []
    split_count = 0
    for line in lines:
        raw_line = str(line or "").rstrip()
        if re.match(r"^\s{32,}\S", raw_line):
            left.append("")
            right.append(raw_line.strip())
            split_count += 1
            continue
        text = raw_line.strip()
        if not text:
            left.append("")
            right.append("")
            continue
        marker_split = re.search(r"\s{3,}((?:\[\d{1,3}\]|\d{1,3}[.\]])\s+\S.*)$", text)
        if marker_split and marker_split.start() >= 24:
            left.append(text[: marker_split.start()].strip())
            right.append(marker_split.group(1).strip())
            split_count += 1
            continue
        match = re.match(r"^(.*?\S)\s{6,}(\S.*)$", text)
        if match:
            left.append(match.group(1).strip())
            right.append(match.group(2).strip())
            split_count += 1
        else:
            left.append(text)
            right.append("")
    if split_count < max(8, len([line for line in lines if str(line or "").strip()]) // 5):
        return lines
    return [line for line in left if line.strip()] + [line for line in right if line.strip()]


def _parse_unnumbered_reference_lines(lines: list[str]) -> list[str]:
    entries: list[str] = []
    current: list[str] = []
    expanded_lines: list[str] = []
    for line in lines:
        expanded_lines.extend(_split_inline_unnumbered_reference_starts(str(line or "")))
    for line in expanded_lines:
        text = str(line or "").replace("\x0c", " ").strip()
        if not text:
            continue
        if _is_reference_page_noise(text):
            if current and _reference_entry_ended(" ".join(current)):
                entries.append(" ".join(current).strip())
                current = []
            continue
        if re.search(r"\bQuestion:\s+Does the paper\b", text, re.I):
            break
        starts_entry = _looks_like_unnumbered_ref_start(text)
        current_text = " ".join(current)
        if current and starts_entry and (_reference_entry_ended(current_text) or _looks_like_completed_reference_for_split(current_text)):
            entries.append(" ".join(current).strip())
            current = [text]
        elif not current:
            if starts_entry or _looks_like_reference_first_line(text):
                current = [text]
        elif _reference_entry_ended(" ".join(current)) and not _looks_like_reference_continuation(text):
            entries.append(" ".join(current).strip())
            current = [text] if starts_entry or _looks_like_reference_first_line(text) else []
        else:
            current.append(text)
    if current:
        entries.append(" ".join(current).strip())
    return [entry for entry in (_trim_reference_entry(item) for item in entries) if len(entry) > 20 and not _looks_like_reference_fragment(entry)]


def _split_inline_unnumbered_reference_starts(line: str) -> list[str]:
    text = re.sub(r"\s+", " ", str(line or "")).strip()
    if len(text) < 220:
        return [line]
    starts = [0]
    author_start = (
        r"(?:"
        r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+,\s+(?:[A-Z]\.|[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+,)"
        r"|[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+(?:\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+){0,3}\s+(?:Team|Collaboration|AI)(?:,|\s)"
        r")"
    )
    pattern = re.compile(
        rf"(?<![A-Z]\.)(?<=\.)\s+(?={author_start}.{{0,260}}\((?:19|20)\d{{2}}[a-z]?\))"
    )
    for match in pattern.finditer(text):
        pos = match.end()
        if pos - starts[-1] >= 80:
            starts.append(pos)
    if len(starts) == 1:
        return [line]
    starts.append(len(text))
    return [text[starts[idx] : starts[idx + 1]].strip() for idx in range(len(starts) - 1) if text[starts[idx] : starts[idx + 1]].strip()]


def _trim_reference_entry(raw: str) -> str:
    value = re.sub(r"\s+", " ", str(raw or "")).strip()
    body_cut = _reference_body_cut_index(value)
    if body_cut is not None:
        return value[:body_cut].strip(" .") + "."
    if len(value) <= 1400:
        return value
    end_patterns = [
        r"^(.{160,}?(?:arXiv:\d{4}\.\d{4,5}(?:v\d+)?(?:\s+\[[^\]]+\])?\.?))",
        r"^(.{160,}?(?:19|20)\d{2}[a-z]?\.)",
        r"^(.{160,}?\bpp\.\s*\d+[-–]\d+\.)",
        r"^(.{160,}?\b\d+[-–]\d+\.)",
    ]
    for pattern in end_patterns:
        match = re.search(pattern, value, re.I)
        if match:
            return match.group(1).strip()
    return value[:1400].rstrip(" ,;") + "..."


def _reference_body_cut_index(value: str) -> int | None:
    stop_patterns = [
        r"\s+(?:Appendix|APPENDIX)\s+[A-Z0-9]\b",
        r"\s+[A-Z]\s+(?:Contributions|Simulation|Real-World|Experiment|Dataset|Additional|Implementation)\b",
        r"\s+(?:Figure|Fig\.|Table|Tab\.)\s+\d+\b",
        r"\s+(?:We calculate|We compute|This task|The robot|The bulk of this data)\b",
        r"\s+A\s+Broader Impact\b",
        r"\s+(?:implemented|datasets|elapsed|below|bag)\.\s+(?:[A-Z]|Task\b)",
        r"\s+Dataset Model Subset\b",
        r"\s+Safety Data Pct\.\b",
        r"\s+maintenance\.\s+[A-Z]",
        r"\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+(?:\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+){1,4}\s+is\s+(?:a|an|the)\s+(?:full\s+)?(?:professor|assistant\s+professor|researcher|phd\s+candidate|senior\s+algorithm\s+engineer)\b",
        r"\s+(?:Publisher[’']s\s+note|Open\s+Access\s+This\s+article|©\s+The\s+Author)",
    ]
    for pattern in stop_patterns:
        match = re.search(pattern, value[160:], re.I)
        if match:
            return 160 + match.start()
    return None


def _looks_like_reference_fragment(raw: str) -> bool:
    value = re.sub(r"\s+", " ", str(raw or "")).strip()
    if re.match(
        r"^(?:Proof\.|Safety Data|Ms\. Pac-Man|Objects\.|Vision\.|Layer Selection Protocol|Video Benchmark Implementation|Evaluation Methodology\.|Intended Use|Published as a conference paper|A\.\s+The|A\.\s+Appendix|C\.\d+\s+|Rel\.\s+Avg\.|Avg\.\s+Success|Tab\.\s+\d+\s+reports|For\s+(?:Pick|Move|Clean|Open|Close|Insert))(?:\b|\s|$)",
        value,
        re.I,
    ):
        return True
    if re.search(r"•\s+Network Architecture\b", value):
        return True
    if re.match(r"^(?:Results\.|instruction of the current goal\b|Split A Split B\b)", value, re.I):
        return True
    if re.search(r"\b(?:We conduct comprehensive experiments|The task sequence starts|Split A Split B|Success Rate)\b", value, re.I):
        return True
    if re.match(r"^\d+\s+https?://", value, re.I):
        return True
    if re.match(r"^(?:preprint|arXiv:|org/|abs/|//|https?://)", value, re.I):
        return True
    if re.match(r"^(?:PMLR\b|Springer International Publishing\b|In:\s+Proceedings\b|Proceedings\b|IEEE/?CVF\b|IEEE\s+International\b|Conference on\b|Journal of\b)", value, re.I):
        return True
    if re.match(r"^(?:ARXIV\.\d|Nov\s+(?:19|20)\d{2}\.|IEEE/?CVF|IEEE\s+International|ACM\s+Conference|Proceedings|Conference on|Journal of|formers\.)", value, re.I):
        return True
    if re.match(r"^(?:in|models|policies|policy|gans)\b", value) and re.search(r"\barXiv\s*:", value, re.I):
        return True
    if re.match(r"^(?:Robosuite|Franka Kitchen|Diffusion Policy,|A\. Contributions\b)", value, re.I):
        return True
    return False


def _looks_like_numbered_reference_line(line: str) -> bool:
    import unicodedata

    text = re.sub(r"^(?:\[\d+\]|\d+\.|\d+\])\s+", "", str(line or "").strip())
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"([A-Z])\.\s*-\s*([A-Z])\.", r"\1.-\2.", text)
    if not text:
        return False
    if re.match(r"^(?:and|or|the|this|that|while|where|when|we|it)\b", text, re.I):
        return False
    # Unicode letters include names such as Michał and decomposed PDF accents.
    # Preserve the same capitalized author-start check without a Latin subset.
    if text[0].isupper() and re.match(r"^[^\W\d_][\w’'`.-]+(?:,|\s+[^\W\d_])", text):
        return True
    if re.match(r"^[A-Z]\.\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+", text):
        return True
    if re.match(r"^[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+(?:,|\s+[A-Z])", text):
        return True
    if re.match(r"^[A-Z][A-Za-z0-9& -]{2,60}\.", text):
        return True
    return False


def _looks_like_unnumbered_ref_start(line: str) -> bool:
    text = str(line or "").strip()
    if re.match(r"^[A-Z](?:\.-[A-Z])?\.\s+\S", text):
        return True
    if re.match(r"^[A-Z]\.\s+[A-Z]\.\s+\S", text):
        return True
    if re.match(r"^[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+,\s+(?:[A-Z]\.\s*){1,4}", text):
        return True
    if re.match(r"^[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+(?:\s+[A-Z]\.?){1,3},\s+[A-Z]", text):
        return True
    if re.match(r"^[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+\s+[A-Z]{1,3},\s+[A-Z]", text):
        return True
    if re.match(r"^[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+\s+[A-Z]{1,4}\s+\((?:19|20)\d{2}[a-z]?\)", text):
        return True
    if re.match(r"^[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+(?:\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+){0,4}\.\s+\S", text):
        return True
    if _looks_like_person_author_line(text):
        return True
    if re.match(r"^[A-Z][A-Za-z-]+(?:\s+[A-Z][A-Za-z-]+){0,3}\s+Collaboration,", text):
        return True
    return False


def _looks_like_reference_first_line(line: str) -> bool:
    text = str(line or "").strip()
    if not text:
        return False
    if re.match(
        r"^(?:IEEE/?CVF|ACM|Proceedings|Conference|International Conference|Journal|Transactions|Advances)\b",
        text,
        re.I,
    ):
        return False
    if re.match(r"^https?://github\.com/[-\w.]+/[-\w.]+/?$", text, re.I):
        return False
    if re.search(r"https?://|arXiv\s*:", text, re.I):
        return True
    if re.search(r"\b(?:preprint|Proceedings|Conference|Journal|Transactions|Advances)\b", text, re.I):
        return True
    if "—" in text and "." in text:
        return True
    return _looks_like_unnumbered_ref_start(text)


def _looks_like_person_author_line(line: str) -> bool:
    text = str(line or "").strip()
    person = r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+(?:\s+(?:[A-Z]\.?|[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+)){0,3}"
    if re.match(rf"^{person},\s+{person},\s+{person}", text):
        return True
    if re.match(rf"^{person}(?:,\s+{person}){{1,}}(?:,?\s+(?:and|et al\.))?", text):
        return True
    if re.match(rf"^{person}\s+(?:and|&)\s+{person}\.", text):
        return True
    return False


def _is_reference_page_noise(line: str) -> bool:
    text = str(line or "").strip()
    if not text:
        return True
    if re.match(
        r"^(?:A\.\s+Appendix|Appendix|Contributors|Authors|Contributor Policy|Optimization|Layer Selection Protocol|Video Benchmark Implementation|Evaluation Methodology|Intended Use|Pct\.\s+Response|Dataset Model Subset|Rel\.\s+Avg\.|Avg\.\s+Success|Tab\.\s+\d+\s+reports|For\s+(?:Pick|Move|Clean|Open|Close|Insert))\b",
        text,
        re.I,
    ):
        return True
    if re.match(r"^\d{1,4}$", text):
        return True
    if re.match(r"^\d+\s+[A-Z][A-Za-z. ]+\s+et al\.$", text):
        return True
    if re.match(r"^(?:Preprint|Published in Transactions on Machine Learning Research.*)$", text, re.I):
        return True
    if re.match(r"^(?:Figure|Fig\.|Table)\s+\d+\b", text, re.I):
        return True
    return False


def _looks_like_reference_continuation(line: str) -> bool:
    text = str(line or "").strip()
    if not text or _is_reference_page_noise(text):
        return False
    if re.match(r"^(?:https?://|//|org/|abs/|preprint|pp\.|volume|doi:|URL\b)", text, re.I):
        return True
    if re.match(r"^[a-z0-9][A-Za-z0-9/_.:#?=&%-]*\.?$", text):
        return True
    return False


def _reference_entry_ended(text: str) -> bool:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    value = re.sub(r"\s+([.,;:])", r"\1", value)
    if re.search(r"\bIn:?\s+(?:19|20)\d{2}[a-z]?\s*$", value, re.I):
        return False
    if re.search(r"\[Accessed\s+\d{1,2}-\d{1,2}-\d{4}\]\.\s*$", value, re.I):
        return True
    if re.search(r"(?:19|20)\d{2}[a-z]?\.\s*(?:URL\s+\S+\.?)?$", value):
        return True
    if re.search(r"(?:19|20)\d{2}[a-z]?[.;]?\s*(?:doi:\s*\S+\.?)?$", value, re.I):
        return True
    if re.search(r"(?:19|20)\d{2}[a-z]?\.\s+(?:[A-Z]\.)?\d+(?:,\s*(?:[A-Z]\.)?\d+)*\s*$", value):
        return True
    if re.search(r"https?://\S+\.?\s*$", value, re.I) and re.search(r"(?:19|20)\d{2}", value[-180:]):
        return True
    if re.search(r"\bURL\s+https?://", value, re.I) and re.search(r"(?:19|20)\d{2}", value[-220:]) and value.endswith("."):
        return True
    if re.search(r"\bCoRR,\s*abs/\d{4}\.\d{4,5}(?:v\d+)?\.?\s*$", value, re.I):
        return True
    if re.search(r"arXiv:\d{4}\.\d{4,5}(?:v\d+)?(?:\s+\[[^\]]+\])?\.?\s*$", value, re.I):
        return True
    if re.search(r"\b(?:pp\.\s*)?\d+[-–]\d+\.?\s*$", value, re.I):
        return True
    if re.search(r"\b(?:Conference on Robot Learning|Annual Conference on Robot Learning|Proceedings of Robotics: Science and Systems \(RSS\)|International Conference on Machine Learning)\.?\s*$", value, re.I):
        return True
    return False


def _looks_like_completed_reference_for_split(text: str) -> bool:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    value = re.sub(r"\s+([.,;:])", r"\1", value)
    if not value.endswith(".") or len(value) < 45:
        return False
    if re.search(r"\((?:19|20)\d{2}[a-z]?\)", value):
        return bool(
            re.search(
                r"\b(?:arXiv|preprint|University|Systems|Letters|Journal|Conference|Proceedings|PMLR|IEEE|Ieee|RSS|ICML|CoRL|CVPR|NeurIPS)\b",
                value[-260:],
                re.I,
            )
        )
    return False


def _guess_ref_title(raw: str) -> str:
    normalized = _normalize_reference_text(raw)
    for rule in _REFERENCE_TITLE_RULES:
        candidate = rule.extract(normalized)
        title = _validated_ref_title_candidate(
            candidate,
            allow_short=rule.allow_short,
            allow_identifier=rule.allow_identifier,
        )
        if title:
            return title
    return ""


def _ref_sentence_parts(raw: str) -> list[str]:
    protected = re.sub(r"(?<![A-Za-z])([A-Z])\.", r"\1<prd>", raw or "")
    parts = re.split(r"\.\s+", protected)
    return [part.replace("<prd>", ".").strip() for part in parts if part.strip()]


def _looks_like_ref_author_list(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    lowered = value.casefold()
    if " et al" in lowered:
        return True
    if value.count(",") >= 2:
        return True
    if "," in value and re.search(r"\b(and|&)\b", lowered):
        return True
    person = r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+(?:\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+){0,3}"
    if re.fullmatch(rf"{person}\s+(?:and|&)\s+{person}", value):
        return True
    if re.fullmatch(r"(?:[A-Z]\.\s+){1,4}[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+", value):
        return True
    return False


def _looks_like_compact_ref_author_list(text: str) -> bool:
    value = str(text or "").strip()
    if not value or len(value) > 520:
        return False
    person = r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+(?:\s+[A-Z]){1,4}"
    if re.fullmatch(rf"{person}(?:,\s+{person}){{0,20}}(?:,\s+et\s+al\.?)?", value):
        return True
    return False


def _clean_ref_title_candidate(value: str) -> str:
    candidate = re.sub(r"\s+", " ", str(value or "")).strip()
    candidate = candidate.strip(" .,:;\"“”")
    arxiv_url = re.search(r"(?:https?:)?//arxiv\.org/abs/(\d{4}\.\d{4,5})(?:v\d+)?", candidate, re.I)
    if arxiv_url and re.match(r"^(?:https?:)?//arxiv\.org/abs/", candidate, re.I):
        return f"arXiv:{arxiv_url.group(1)}"
    candidate = re.sub(r"^(?:et al\.?\s*)+", "", candidate, flags=re.I).strip()
    candidate = re.sub(r"\b(arXiv|CoRR)\s+preprint\b.*$", "", candidate, flags=re.I)
    candidate = re.sub(r"\bPreprint\s+at\s+https?://.*$", "", candidate, flags=re.I)
    candidate = re.sub(r"\bPreprint\s+at\s+https?\s*:\s*//.*$", "", candidate, flags=re.I)
    candidate = re.sub(r"\bURL\s+https?://.*$", "", candidate, flags=re.I)
    candidate = re.sub(r"\bURL\s+https?\s*:\s*//.*$", "", candidate, flags=re.I)
    candidate = re.sub(r"\s+https?://.*$", "", candidate, flags=re.I)
    candidate = re.sub(r"\s+https?\s*:\s*//.*$", "", candidate, flags=re.I)
    candidate = re.sub(r"\s+www\.\s*.*$", "", candidate, flags=re.I)
    candidate = re.sub(r",?\s+DOI\b.*$", "", candidate)
    candidate = re.sub(r"\s+In\s+(?:Proceedings|Conference|International|European|The)\b.*$", "", candidate, flags=re.I)
    candidate = re.sub(r",?\s+pp\.\s*\d+\s*[-–]\s*\d+.*$", "", candidate, flags=re.I)
    candidate = re.sub(r",?\s+(?:19|20)\d{2}\.?\s*$", "", candidate)
    return candidate.strip(" .,:;\"“”")


def _looks_like_ref_venue_tail(value: str) -> bool:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return False
    if re.match(r"^(?:[A-Z][A-Za-z0-9&'.:-]+(?:\s+[A-Z][A-Za-z0-9&'.:-]+){0,5}|[A-Z]{2,})\s*,\s*\d", text):
        return True
    if re.match(r"^(?:In\s+)?(?:Proceedings|Conference|Journal|Transactions|Science|Nature|Springer|IEEE|ACM|PMLR|F1000Research|Technologies)\b", text, re.I):
        return True
    if re.search(r"\b(?:ISBN|ISSN)\b", text, re.I):
        return True
    return False


@dataclass(frozen=True, slots=True)
class _ReferenceTitlePattern:
    name: str
    pattern: Pattern[str]
    title_group: int = 2
    prefix_group: int | None = 1
    prefix_mode: str = "author"


@dataclass(frozen=True, slots=True)
class _ReferenceTitleRule:
    name: str
    extract: Callable[[str], str]
    allow_short: bool = False
    allow_identifier: bool = False


_REFERENCE_VENUE_BOUNDARY = (
    r"(?:In:?|arXiv\b|CoRR\b|doi:|URL\b|https?://|Journal\b|Proceedings\b|"
    r"Conference\b|IEEE\b|ACM\b|PMLR\b|Nature\b|Science\b|Springer\b|"
    r"Advances\b|Transactions\b|NeurIPS\b|ICLR\b|ICML\b|CVPR\b|ECCV\b|"
    r"[A-Z][A-Za-z -]{3,90}(?:[,.;]|\s+\d|\s+\())"
)
_REFERENCE_TITLE_PATTERNS: tuple[_ReferenceTitlePattern, ...] = (
    _ReferenceTitlePattern(
        "author-colon-title",
        re.compile(
            rf"^(.{{5,1200}}?):\s+(.+?)(?:\.\s+{_REFERENCE_VENUE_BOUNDARY}|\s+\((?:19|20)\d{{2}}\)|$)",
            re.I,
        ),
        prefix_mode="author-colon",
    ),
    _ReferenceTitlePattern(
        "author-dot-year-title",
        re.compile(
            rf"^(.{{4,1400}}?)\.\s+(?:19|20)\d{{2}}[a-z]?\.\s+(.+?)(?:\.\s+{_REFERENCE_VENUE_BOUNDARY}|$)",
            re.I,
        ),
        prefix_mode="author-loose",
    ),
    _ReferenceTitlePattern(
        "author-parenthesized-year-title",
        re.compile(
            rf"^(.{{4,520}}?)\s+\((?:19|20)\d{{2}}(?:,\s*[A-Za-z]+)?[a-z]?\)\.?\s+(.+?)(?:\.\s+{_REFERENCE_VENUE_BOUNDARY}|$)",
            re.I,
        ),
        prefix_mode="author-loose",
    ),
    _ReferenceTitlePattern(
        "initials-title",
        re.compile(
            rf"^(.{{5,900}}\b[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+,\s+(?:[A-Z]\.\s*){{1,4}})\s+(.+?)(?:\.\s+{_REFERENCE_VENUE_BOUNDARY}|$)",
            re.I,
        ),
        prefix_mode="initials",
    ),
    _ReferenceTitlePattern(
        "compact-authors",
        re.compile(
            rf"^(.{{4,1400}}?)\.\s+(.+?)(?:\.\s+{_REFERENCE_VENUE_BOUNDARY}|\s+\((?:19|20)\d{{2}}\)|$)",
            re.I,
        ),
        prefix_mode="author-loose",
    ),
    _ReferenceTitlePattern(
        "et-al-title",
        re.compile(
            rf"^(.+?\bet\s+al)\.\s+(.+?)(?:\.\s+{_REFERENCE_VENUE_BOUNDARY}|$)",
            re.I,
        ),
        prefix_mode="et-al",
    ),
    _ReferenceTitlePattern(
        "typed-reference",
        re.compile(r"^(.{6,420}?)\.\s+(.+?)\s*\[[A-Z]\](?://)?(?:\s*In:|\s*$)", re.I),
    ),
    _ReferenceTitlePattern(
        "initial-author-comma-title",
        re.compile(
            r"^(?:(?:[A-Z]\.\s*)+[A-Z][\w’'`-]+(?:\s+[A-Z][\w’'`-]+)*,\s*)+"
            r"(.+?)(?:,\s+(?:19|20)\d{2}\b|,\s+(?:arXiv|IEEE|ACM|Nature|Science)\b|$)"
        ),
        title_group=1,
        prefix_group=None,
        prefix_mode="initials",
    ),
    _ReferenceTitlePattern(
        "comma-journal",
        re.compile(
            r"^(.{4,360}?),\s+(.+?),\s+(?:(?:Phys\.|Rev\.|J\.|Proc\.|Annu\.|IEEE|ACM|Nature|Science)(?:\s+[A-Za-z.]+){0,4}\b|[A-Z][A-Za-z -]{3,80},)"
        ),
        prefix_mode="author-loose",
    ),
)


def _normalize_reference_text(raw: str) -> str:
    import unicodedata

    value = re.sub(r"(\w)-\s+(\w)", r"\1\2", unicodedata.normalize("NFC", raw or ""))
    value = re.sub(r"(\d)\.\s+(\d)", r"\1.\2", value)
    return re.sub(r"^(?:\[\d+\]|\d+\.|\d+\])\s+", "", value).strip()


def _validated_ref_title_candidate(
    value: object,
    *,
    allow_short: bool = False,
    allow_identifier: bool = False,
) -> str:
    candidate = _clean_ref_title_candidate(str(value or ""))[:220]
    if not candidate:
        return ""
    if allow_identifier and re.fullmatch(r"arXiv:\d{4}\.\d{4,5}", candidate, re.I):
        return candidate
    alphanumeric = re.sub(r"[^A-Za-z0-9]+", "", candidate)
    if len(alphanumeric) < (4 if allow_short else 8):
        return ""
    if _looks_like_bad_ref_title(candidate) or _looks_like_ref_venue_tail(candidate):
        return ""
    if re.fullmatch(
        r"(?:doi:?\s*)?10\.\S+|(?:URL\s+)?https?://\S+|(?:19|20)\d{2}[a-z]?",
        candidate,
        re.I,
    ):
        return ""
    return candidate


def _reference_prefix_matches(prefix: str, mode: str) -> bool:
    value = str(prefix or "").strip()
    lowered = value.casefold()
    if mode == "et-al":
        return bool(re.search(r"\bet\s+al\.?$", value, re.I))
    if mode == "initials":
        return not re.match(r"^[A-Z]\.\s+", value) and (
            "," in value or " and " in lowered
        )
    if mode == "author-colon":
        return (
            ". " not in value
            and not re.search(r"\.\s+(?:19|20)\d{2}[a-z]?\.\s+", value)
            and _reference_prefix_matches(value, "author-loose")
        )
    if _looks_like_ref_author_list(value) or _looks_like_compact_ref_author_list(value):
        return True
    words = value.split()
    person_name = 1 <= len(words) <= 4 and all(
        word[0].isupper() and re.fullmatch(r"[^\W\d_][\w’'`.-]*", word)
        for word in words
    )
    return mode == "author-loose" and (
        value.count(",") >= 1
        or bool(re.search(r"\b(?:and|et\s+al)\b", lowered))
        or person_name
    )


def _extract_title_before_doi(raw: str) -> str:
    doi = re.search(r"\bdoi\s*:?\s*10\.\S+", raw, re.I)
    if not doi:
        return ""
    before_doi = raw[:doi.start()].strip(" .,:;")
    parts = _ref_sentence_parts(before_doi)
    candidate = parts[-1] if parts else before_doi
    if len(parts) >= 2 and _looks_like_ref_venue_tail(candidate):
        candidate = parts[-2]
    return candidate


def _extract_open_x_title(raw: str) -> str:
    if re.match(r"^(?:Open\s+X-Embodiment\s+Collaboration|Collaboration,\s+O\.\s*X\.-E\.)", raw, re.I):
        return "Open X-Embodiment: Robotic learning datasets and RT-X models"
    return ""


def _first_quoted_title(raw: str) -> tuple[str, int]:
    # Apostrophes in authors (O'Brien, d'Autume) are not title delimiters.
    match = re.search(r'"([^"\n]+)"|“([^”\n]+)”|(?<!\w)[\'‘]([^\'’\n]+)[\'’](?!\w)', raw)
    if match and raw[match.start()] == "“":
        depth = 1
        for index in range(match.start() + 1, len(raw)):
            if raw[index] == "“":
                depth += 1
            elif raw[index] == "”":
                depth -= 1
                if depth == 0:
                    return raw[match.start() + 1:index], match.start()
    return (next(group for group in match.groups() if group is not None), match.start()) if match else ("", -1)


def _extract_author_quoted_title(raw: str) -> str:
    candidate, start = _first_quoted_title(raw)
    if start < 0 or start >= 900:
        return ""
    prefix = raw[:start]
    if _reference_prefix_matches(prefix, "author-loose"):
        return candidate
    return ""


def _extract_pattern_title(raw: str) -> str:
    protected = re.sub(r"(?<![A-Za-z])([A-Z])\.", r"\1<prd>", raw)
    for rule in _REFERENCE_TITLE_PATTERNS:
        # Broad author/title boundaries must not split at a middle initial.
        # The dedicated initials rule deliberately consumes those periods.
        match = rule.pattern.match(raw if rule.prefix_mode == "initials" else protected)
        if not match:
            continue
        if rule.prefix_group is not None and not _reference_prefix_matches(
            match.group(rule.prefix_group).replace("<prd>", "."), rule.prefix_mode
        ):
            continue
        candidate = match.group(rule.title_group).replace("<prd>", ".")
        if _validated_ref_title_candidate(candidate):
            return candidate
    return ""


def _extract_sentence_title(raw: str) -> str:
    parts = _ref_sentence_parts(raw)
    if len(parts) < 2:
        return ""
    if _reference_prefix_matches(parts[0], "author-loose"):
        title_index = 2 if len(parts) >= 3 and re.fullmatch(r"(?:19|20)\d{2}[a-z]?", parts[1]) else 1
        if title_index < len(parts):
            return parts[title_index]
    if len(parts) >= 3 and _reference_prefix_matches(parts[0], "author") and _looks_like_ref_venue_tail(parts[2]):
        return parts[1]
    if re.match(r"^(?:URL|https?://)", parts[1], re.I) and not _looks_like_ref_author_list(parts[0]):
        return parts[0]
    if len(parts[0].split()) <= 4 and re.fullmatch(r"[A-Z][A-Za-z0-9& -]{1,60}", parts[0]):
        return parts[1]
    return ""


def _extract_typed_marker_title(raw: str) -> str:
    marker = re.search(r"\s\[[A-Z]\](?://)?(?:\.?\s*In:|\s*$)", raw, re.I)
    if not marker:
        return ""
    parts = _ref_sentence_parts(raw[:marker.start()])
    return parts[-1] if len(parts) >= 2 and _reference_prefix_matches(parts[0], "author") else ""


def _extract_colon_title(raw: str) -> str:
    match = re.search(
        rf":\s+(?!https?://)(.+?)(?:\.\s+{_REFERENCE_VENUE_BOUNDARY}|\s+\(\d{{4}}\)|$)",
        raw,
        re.I,
    )
    if not match:
        return ""
    prefix = raw[:match.start()]
    return match.group(1) if _reference_prefix_matches(prefix, "author-loose") else ""


def _extract_year_title(raw: str) -> str:
    match = re.search(
        rf"\((?:19|20)\d{{2}}[a-z]?\)\.?\s+(.+?)(?:\.\s+{_REFERENCE_VENUE_BOUNDARY}|$)",
        raw,
        re.I,
    )
    return match.group(1) if match else ""


def _extract_open_quote_title(raw: str) -> str:
    match = re.search(r"[\"“]([^\"”]{8,260}?)(?:,?\s+arXiv|\.\s+(?:19|20)\d{2}|$)", raw, re.I)
    return match.group(1) if match and match.start() < 900 else ""


def _extract_any_quoted_title(raw: str) -> str:
    candidate, _ = _first_quoted_title(raw)
    return candidate


def _extract_sentence_fallback_title(raw: str) -> str:
    for part in _ref_sentence_parts(raw)[:4]:
        if not re.search(r"\b(?:arXiv|doi|https?|pages?|pp|Tech\.?\s+rep)\b", part, re.I):
            return part
    return ""


_REFERENCE_TITLE_RULES: tuple[_ReferenceTitleRule, ...] = (
    _ReferenceTitleRule("open-x-canonical", _extract_open_x_title),
    _ReferenceTitleRule("author-quoted", _extract_author_quoted_title),
    _ReferenceTitleRule("doi-tail", _extract_title_before_doi, allow_identifier=True),
    _ReferenceTitleRule("structured-pattern", _extract_pattern_title),
    _ReferenceTitleRule("sentence-structure", _extract_sentence_title),
    _ReferenceTitleRule("typed-marker", _extract_typed_marker_title),
    _ReferenceTitleRule("author-colon", _extract_colon_title),
    _ReferenceTitleRule("year-boundary", _extract_year_title),
    _ReferenceTitleRule("open-quote", _extract_open_quote_title),
    _ReferenceTitleRule("quoted-fallback", _extract_any_quoted_title),
    _ReferenceTitleRule("sentence-fallback", _extract_sentence_fallback_title),
)


def _match_registered_paper(title: str, raw: str, papers: list[dict]) -> dict | None:
    hay = f"{title} {raw}".casefold()
    for paper in papers:
        ptitle = str(paper.get("title") or "").casefold()
        if ptitle and (ptitle in hay or (title and title.casefold() in ptitle)):
            return paper
    return None


def register_ref(payload: dict, actor: str) -> dict:
    ref_id = str(payload.get("ref_id") or "").strip()
    source_paper_id = str(payload.get("paper_id") or "").strip()
    if not ref_id:
        raise ValueError("ref_id required")
    with LOCK:
        source_paper, ref, refs = _ref_context(ref_id, source_paper_id)
        papers = _all_papers()
        existing_id = str(ref.get("registered_paper_id") or "").strip()
        existing = next((paper for paper in papers if str(paper.get("id") or "") == existing_id), None)
        if existing:
            return {"ref": ref, "paper": _paper_public(existing), "detail": get_paper_detail(existing["id"])}
    metadata = _lookup_ref_metadata(ref)
    if not metadata:
        raise ValueError("Could not locate this reference on arXiv")
    with LOCK:
        papers = _all_papers()
        existing = _find_existing_paper_for_ref(metadata, papers)
        if existing:
            paper = existing
        else:
            paper = PAPER_STORE.register(_paper_payload_from_ref_metadata(metadata), actor)
        updated_ref = None
        now = _now()
        for idx, item in enumerate(refs):
            if str(item.get("ref_id") or "") == ref_id:
                updated_ref = {
                    **item,
                    "title": str(item.get("title") or metadata.get("title") or "").strip(),
                    "registered_paper_id": paper["id"],
                    "registered_title": paper["title"],
                    "registered_by": actor,
                    "registered_at": now,
                    "lookup_source": metadata.get("source_url") or "",
                }
                refs[idx] = updated_ref
                break
        if not updated_ref:
            raise KeyError("ref not found")
        _write_store_refs(str(source_paper.get("id") or updated_ref.get("paper_id") or ""), refs)
    return {"ref": updated_ref, "paper": _paper_public(paper), "detail": get_paper_detail(paper["id"])}


def _lookup_ref_metadata(ref: dict) -> dict:
    raw = str(ref.get("raw") or "")
    title = str(ref.get("title") or "").strip()
    arxiv_id = _arxiv_id_from_text(f"{title}\n{raw}")
    if arxiv_id:
        return _fetch_arxiv_metadata(id_list=arxiv_id)
    if title:
        return _fetch_arxiv_metadata(search_title=title)
    guessed = _guess_ref_title(raw)
    if guessed:
        return _fetch_arxiv_metadata(search_title=guessed)
    return {}


def _arxiv_id_from_text(text: str) -> str:
    patterns = [
        r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})(?:v\d+)?",
        r"\barXiv\s*:\s*([0-9]{4}\.[0-9]{4,5})(?:v\d+)?",
        r"\b([0-9]{4}\.[0-9]{4,5})(?:v\d+)?\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "", re.I)
        if match:
            return match.group(1)
    return ""


def _fetch_arxiv_metadata(*, id_list: str = "", search_title: str = "") -> dict:
    params = {"start": "0", "max_results": "1"}
    if id_list:
        params["id_list"] = id_list
    elif search_title:
        params["search_query"] = f'ti:"{search_title[:180]}"'
    else:
        return {}
    url = f"https://export.arxiv.org/api/query?{urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "TFIPortal/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            body = response.read(1_500_000)
    except (urllib.error.URLError, TimeoutError, OSError):
        return {}
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return {}
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entry = root.find("atom:entry", ns)
    if entry is None:
        return {}
    return _metadata_from_arxiv_entry(entry, ns)


def _metadata_from_arxiv_entry(entry, ns: dict[str, str]) -> dict:
    def text(path: str) -> str:
        node = entry.find(path, ns)
        return re.sub(r"\s+", " ", node.text or "").strip() if node is not None else ""

    entry_id = text("atom:id")
    arxiv_id = _arxiv_id_from_text(entry_id)
    pdf_url = ""
    for link in entry.findall("atom:link", ns):
        if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
            pdf_url = link.attrib.get("href", "")
            break
    if not pdf_url and arxiv_id:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    authors = []
    for author in entry.findall("atom:author", ns):
        name = author.find("atom:name", ns)
        if name is not None and str(name.text or "").strip():
            authors.append(re.sub(r"\s+", " ", name.text or "").strip())
    published = text("atom:published")
    return {
        "title": text("atom:title"),
        "authors": ", ".join(authors),
        "year": _safe_year(published),
        "abstract": text("atom:summary"),
        "arxiv_id": arxiv_id,
        "pdf_url": pdf_url,
        "source_url": entry_id or (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""),
    }


def _paper_payload_from_ref_metadata(metadata: dict) -> dict:
    arxiv_id = str(metadata.get("arxiv_id") or "").strip()
    tags = ["reference"]
    if arxiv_id:
        tags.append("arxiv")
    return {
        "title": metadata.get("title") or "Untitled reference",
        "authors": metadata.get("authors") or "",
        "venue": "arXiv" if arxiv_id else "Reference",
        "year": metadata.get("year") or "",
        "session": "References",
        "abstract": metadata.get("abstract") or "",
        "tags": tags,
        "pdf_path": metadata.get("pdf_url") or "",
        "status": "registered_from_ref",
    }


def _find_existing_paper_for_ref(metadata: dict, papers: list[dict]) -> dict | None:
    title = str(metadata.get("title") or "").casefold()
    arxiv_id = str(metadata.get("arxiv_id") or "").casefold()
    for paper in papers:
        hay = "\n".join(str(paper.get(key) or "") for key in ("id", "slug", "title", "abstract", "pdf_path")).casefold()
        if arxiv_id and arxiv_id in hay:
            return paper
        ptitle = str(paper.get("title") or "").casefold()
        if title and (title == ptitle or title in ptitle or ptitle in title):
            return paper
    return None


def _chat_workspace_display(paper: dict | None, collection_label: str) -> dict:
    """What the Research Chat shows for one paper or for a collection."""
    if paper:
        return {
            "title": "Research Chat",
            "subtitle": str(paper.get("title") or "Selected paper"),
            "placeholder": "Ask about this paper...",
            "empty_text": "Ask about this paper.",
            "prompt_context": "",
        }
    return {
        "title": "Research Chat",
        "subtitle": collection_label or "Library Chat",
        "placeholder": "Ask across the paper library...",
        "empty_text": "Compare or summarize the papers in this collection.",
        "prompt_context": "",
    }


def chat_workspace_display_context(*, paper_id: str = "", collection_label: str = "") -> dict:
    paper_id = str(paper_id or "").strip()
    return _chat_workspace_display(PAPER_STORE.paper_by_id(paper_id) if paper_id else None, collection_label)


def chat_workspace_prompt_context(
    *,
    paper_id: str = "",
    query: str = "",
    paper_ids: list[str] | None = None,
    collection_items: list[dict] | None = None,
    collection_label: str = "",
    collection_summary: str = "",
    collection_mode: str = "",
    collection_query: str = "",
    collection_filters: dict | None = None,
) -> dict:
    paper_id = str(paper_id or "").strip()
    query = str(query or "").strip()
    requested_ids = [str(item or "").strip() for item in (paper_ids or []) if str(item or "").strip()]
    requested_items = [item for item in (collection_items or []) if isinstance(item, dict)]
    papers = resource_index_papers()
    with LOCK:
        selected = PAPER_STORE.paper_by_id(paper_id) if paper_id else None
        if selected:
            matches = []
        elif requested_ids:
            by_id = {str(p.get("id") or ""): p for p in papers}
            matches = [by_id[item] for item in requested_ids if item in by_id]
        else:
            matches = _filter_papers(papers, {"q": query})
        if not selected and not matches and not requested_items:
            matches = [_paper_public(p) for p in papers[:10]]
    if selected:
        public = _paper_public(selected)
        analysis_path = _paper_store_file(selected, "analysis") or _safe_file_path(str(selected.get("analysis_path") or ""))
        pdf_path = _safe_file_path(str(selected.get("pdf_path") or ""))
        pdf_text_path = _paper_store_file(selected, "pdf_text_clean")
        prompt_context = f"""Paper Research Chat context:
- selected paper id: {public.get('id') or ''}
- title: {public.get('title') or ''}
- authors: {public.get('authors') or ''}
- venue/year: {public.get('venue') or ''} {public.get('year') or ''}
- paper root: {_paper_root(selected) or ''}
- pdf path: {pdf_path or selected.get('pdf_path') or ''}
- analysis path: {analysis_path or selected.get('analysis_path') or ''}
- pdf text path: {pdf_text_path or ''}

Abstract:
{str(selected.get('abstract') or '')[:3000]}

Summary excerpt:
{_summary_text(selected, limit=2400)}

Analysis excerpt:
{_analysis_text(selected, limit=12000)}
"""
        return {**_chat_workspace_display(public, collection_label), "prompt_context": prompt_context}
    lines = [
        "Paper Research Chat context:",
        "- selected paper: none",
        f"- collection label: {collection_label or 'Paper Library'}",
        f"- collection mode: {collection_mode or 'library'}",
        f"- collection query: {collection_query or ''}",
        f"- collection filters: {json.dumps(collection_filters or {}, ensure_ascii=False)}",
        f"- visible papers in collection: {max(len(matches), len(requested_items))}",
        f"- paper store root: {PAPER_STORE_ROOT}",
        "",
    ]
    if collection_summary:
        lines.extend(["Collection summary:", str(collection_summary)[:3000], ""])
    lines.extend([
        "Candidate papers from current query:",
    ])
    for paper in matches[:30]:
        public = _paper_public(paper)
        lines.append(
            f"- {public.get('id') or ''}: {public.get('title') or ''} "
            f"({public.get('venue') or ''} {public.get('year') or ''})"
        )
        summary = _summary_text(paper, limit=500) or str(public.get("abstract") or "")[:500]
        if summary:
            lines.append(f"  summary: {summary}")
        root = _paper_root(paper)
        if root:
            lines.append(f"  root: {root}")
    if requested_items:
        lines.extend(["", "Collection entries from the current Portal view:"])
        for item in requested_items[:80]:
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            authors = str(item.get("authors") or "").strip()
            venue = str(item.get("venue") or "").strip()
            year = str(item.get("year") or "").strip()
            doi = str(item.get("doi") or "").strip()
            url = str(item.get("url") or "").strip()
            registered_id = str(item.get("registered_paper_id") or "").strip()
            lines.append(f"- {title}")
            details = []
            if authors:
                details.append(f"authors: {authors}")
            if venue or year:
                details.append(f"venue/year: {venue} {year}".strip())
            if doi:
                details.append(f"doi: {doi}")
            if url:
                details.append(f"url: {url}")
            if registered_id:
                details.append(f"registered paper id: {registered_id}")
            if details:
                lines.append(f"  {'; '.join(details)}")
    return {**_chat_workspace_display(None, collection_label), "prompt_context": "\n".join(lines)}


def api_docs() -> dict:
    return {
        "endpoints": [
            {"method": "GET", "path": "/paper/api/library", "purpose": "List visible papers, filters, folder tree, workflow metadata, and the current actor's favorite_ids. favorite=1 intersects favorites with other filters."},
            {"method": "POST", "path": "/paper/api/favorites", "purpose": "Set the authenticated actor's personal Paper favorite with {paper_id, favorite: boolean}. Adding requires read access; removal remains possible after access loss."},
            {"method": "GET", "path": "/paper/api/papers/detail?id=...", "purpose": "Load one paper, analysis markdown, PDF URL, and reviews."},
            {"method": "POST", "path": "/paper/api/papers/registration-draft", "purpose": "Upload a PDF and start extracting editable registration metadata."},
            {"method": "GET", "path": "/paper/api/papers/registration-draft?upload_id=...", "purpose": "Read PDF registration metadata extraction status."},
            {"method": "POST", "path": "/paper/api/papers/register", "purpose": "Register metadata plus a PDF registration upload, PDF/analysis paths, or URLs."},
            {"method": "POST", "path": "/paper/api/papers/delete-pdf", "purpose": "Delete the current local PDF file and derived PDF text cache without deleting the paper record."},
            {"method": "POST", "path": "/paper/api/reviews/create", "purpose": "Create a three-lens review draft for a paper."},
            {"method": "POST", "path": "/paper/api/reviews/start", "purpose": "Start or reuse the current actor's background review without waiting for the model."},
            {"method": "GET", "path": "/paper/api/reviews/status?paper_id=...", "purpose": "Recover the current actor's latest review job after reload; render results from paper reviews."},
            {"method": "POST", "path": "/paper/api/deep-read/start", "purpose": "Start a background PDF deep-read job and write analysis markdown."},
            {"method": "GET", "path": "/paper/api/deep-read/status?paper_id=...", "purpose": "Poll deep-read job status for a paper."},
            {"method": "POST", "path": "/paper/api/figures/extract", "purpose": "Extract embedded PDF figures and save figure metadata."},
            {"method": "POST", "path": "/paper/api/figures/explain", "purpose": "Start a background caption/context based explanation job for one figure."},
            {"method": "POST", "path": "/paper/api/figures/generate", "purpose": "Start a background image generation job for one figure (paper:figure-generation)."},
            {"method": "POST", "path": "/paper/api/figures/generated/delete", "purpose": "Delete one generated figure record and its stored image file."},
        ],
        "data_root": str(DATA_ROOT),
        "workflow": WORKFLOW_STEPS,
    }
