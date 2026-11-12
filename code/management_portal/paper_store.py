"""The Paper package store: roots, manifests, package files, PDF text and run records."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
import re
import shutil
import threading
import time
from urllib.parse import urlparse
import urllib.request

from management_common.instance_manifest import AREAS
from management_common.instance_locations import current_instance
from management_common.portal_paths import path as portal_path
from management_common.scan_cancellation import check_scan_cancelled, walk_paths
from management_portal import paper_state_store
from management_portal.notifications import publish_local_projection_change
from management_portal.paper_pdf import _extract_pdf_text_file, _has_pdf_header
from management_portal.paper_records import (
    _arxiv_id_from_text, _coerce_timestamp, _is_non_project_tag, _is_project_relation_id, _is_project_tag,
    _is_url, _merge_paper_relations, _merge_project_tags, _normalize_fingerprint_text, _normalize_list,
    _normalize_venue, _now, _paper_authors_list, _paper_authors_string, _project_ids_from_paper, _safe_year,
    _slugify,
)
from management_portal.resources import get_resource_service
from management_portal.shared_state import atomic_replace_json, exclusive_file_lock


LOGGER = logging.getLogger(__name__)
WORKSPACE_ROOT = current_instance().code_root()
DATA_ROOT = portal_path("features") / "paper_workspace"
PAPER_STORE_ROOT = portal_path('papers')
# Reviews, notes, job records and curated venues live in paper_state_store.


PAPER_RUN_LOCK_ROOT = PAPER_STORE_ROOT / "_work" / "run_locks"


PDF_TEXT_ROOT = DATA_ROOT / "pdf_text"


LOCK = threading.RLock()


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


ALLOWED_FILE_ROOTS = [
    *(portal_path(area) for area in AREAS),
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


def _validated_paper_id(paper_id: object) -> str:
    paper_id = str(paper_id or "").strip()
    try:
        get_resource_service().validate_id(paper_id, "paper")
    except ValueError:
        raise ValueError(f"invalid Paper ID: {paper_id or '<empty>'}")
    return paper_id


def _manifest_matches_paper_address(manifest: dict, address: str) -> bool:
    return str(manifest.get("paper_id") or "").strip() == address


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
        for path in (self.items_root, self.index_root, self.root / "_incoming", self.root / "_work"):
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
                from management_common.directory_cleanup_store import get_directory_cleanup_store
                from .resources.directory_registrations import is_deleted
                for path in walk_paths(year):
                    if path.name != 'manifest.json' or get_directory_cleanup_store().pending_at(path):
                        continue
                    if is_deleted(str(self.read_manifest(path).get('paper_id') or '')):
                        continue
                    manifests.append(path)
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

    def manifest_for_paper_id(self, paper_id: str, *, allow_scan: bool = True, registered_only: bool = False) -> tuple[Path, dict] | None:
        from .resources.directory_registrations import is_deleted
        from management_common.directory_cleanup_store import get_directory_cleanup_store
        if is_deleted(paper_id):
            return None
        try:
            target = _validated_paper_id(paper_id)
        except ValueError:
            return None
        from .resources.registry_store import get_registry_store
        from management_common.instance_locations import located_path
        registration = get_registry_store().get('paper', target)
        if registration is not None:
            path = Path(located_path(registration['directories'][0]['path'])) / 'manifest.json'
            if get_directory_cleanup_store().pending_at(path) or not path.is_file():
                return None
            manifest = self.read_manifest(path)
            return (path, manifest) if _manifest_matches_paper_address(manifest, target) else None
        if registered_only:
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
            if indexed_manifest_path.is_file() and not get_directory_cleanup_store().pending_at(indexed_manifest_path):
                manifest = self.read_manifest(indexed_manifest_path)
                if _manifest_matches_paper_address(manifest, target):
                    return indexed_manifest_path, manifest
        if not allow_scan:
            return None
        for indexed_manifest_path in self.manifest_paths():
            manifest = self.read_manifest(indexed_manifest_path)
            if _manifest_matches_paper_address(manifest, target):
                return indexed_manifest_path, manifest
        return None

    def paper_dir_from_manifest(self, manifest: dict, manifest_path: Path | None = None) -> Path:
        from .resources.registry_store import get_registry_store
        from management_common.instance_locations import located_path
        registration = get_registry_store().get('paper', str(manifest.get('paper_id') or ''))
        if registration is not None:
            return Path(located_path(registration['directories'][0]['path']))
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
        from .resources.registry_store import get_registry_store
        registration = get_registry_store().get('paper', paper['id'])
        if registration:
            paper['directory'] = registration['directory']
        return paper

    def all_papers(self) -> list[dict]:
        from .resources.directory_registrations import register_owned
        papers = []
        for path in self.manifest_paths():
            manifest = self.read_manifest(path)
            if not manifest:
                continue
            try:
                paper = self.manifest_to_paper(manifest, path)
                registration = register_owned('paper', paper['id'], [self.paper_dir_from_manifest(manifest, path)], scanning=True)
                if registration is None:
                    continue
                paper['directory'] = registration['directory']
                papers.append(paper)
            except Exception:
                LOGGER.warning("Paper manifest %s could not be read; the paper is left out", path, exc_info=True)
                continue
        return papers

    def paper_by_id(self, paper_id: str, *, allow_scan: bool = True) -> dict | None:
        found = self.manifest_for_paper_id(paper_id, allow_scan=allow_scan)
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
        return base.with_name(base.name + '__' + str(paper['id']))

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
        from .resources.registry_store import get_registry_store
        fp_map = self.fingerprint_map()
        for fp in self.fingerprints_for_payload(payload):
            paper_id = str(fp_map.get(fp) or "").strip()
            # An index can observe a published package before the owner commits.
            # Content matching never turns that interrupted creation into an
            # already held Paper or takes over an unregistered user package.
            if paper_id and get_registry_store().get('paper', paper_id) is not None:
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

    def write_manifest(self, paper: dict, paper_dir: Path, existing: dict | None = None, *, connection=None, operation_id='') -> dict:
        from .resources.directory_registrations import directory_writer, register_owned
        from .resources.registry_store import get_registry_store
        if connection is None:
            if operation_id or get_registry_store().get('paper', paper['id']) is None:
                from .resources.creation import Creation
                creation = Creation(paper['id'], actor=str(paper.get('created_by') or ''), payload=paper, operation_id=operation_id)
                with creation.prepare([(paper_dir, 'directory')]):
                    if creation.completed:
                        previous = get_registry_store().get('paper', paper['id'])
                        if previous is None:
                            raise KeyError(paper['id'])
                        from management_common.instance_locations import located_path
                        manifest_path = Path(located_path(previous['directories'][0]['path'])) / 'manifest.json'
                    else:
                        def initialize(index, stage):
                            for subdir in ('text', 'reviews', 'notes', 'refs', 'figures', 'runs'):
                                (stage / subdir).mkdir()
                            manifest = self.manifest_from_paper(paper, stage, existing=existing)
                            manifest['root'] = str(creation.paths[0])
                            _write_json(stage / 'manifest.json', manifest)
                        creation.publish(initialize)
                        with directory_writer('paper') as writer, creation.registration(writer.connection):
                            register_owned('paper', paper['id'], creation.paths, connection=writer.connection)
                        manifest_path = creation.paths[0] / 'manifest.json'
                    self.ensure()
                    manifest = self.read_manifest(manifest_path)
                    if not manifest or manifest.get('paper_id') != paper['id']:
                        raise ValueError('Registered Paper manifest is missing or has a different identity')
                    return manifest
            with directory_writer('paper') as writer:
                return self.write_manifest(paper, paper_dir, existing, connection=writer.connection)
        with get_registry_store().writer('paper', connection=connection) as writer:
            if writer.get('paper', paper['id']) is None:
                raise RuntimeError('Prepare Paper creation before opening its registration transaction')
        register_owned('paper', paper['id'], [paper_dir], connection=connection)
        self.ensure()
        paper_dir.mkdir(parents=True, exist_ok=True)
        for subdir in ("text", "reviews", "notes", "refs", "figures", "runs"):
            (paper_dir / subdir).mkdir(parents=True, exist_ok=True)
        manifest = self.manifest_from_paper(paper, paper_dir, existing=existing)
        _write_json(paper_dir / "manifest.json", manifest)
        return manifest

    def ingest_file(self, source: str, target: Path, *, retries: int = 3, retry_backoff_s: float = 2.0,
                    require_pdf: bool = False) -> bool:
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
                        if require_pdf and not _has_pdf_header(data):
                            raise ValueError("remote PDF did not return PDF bytes")
                        tmp.write_bytes(data)
                        tmp.replace(target)
                        return True
                    last_error = RuntimeError("empty response")
                except Exception as exc:
                    last_error = exc
                    try:
                        tmp.unlink(missing_ok=True)
                    except OSError:  # the download error above is the one reported
                        pass
                if attempt + 1 < attempts:
                    time.sleep(max(0.0, float(retry_backoff_s)) * (attempt + 1))
            raise RuntimeError(f"download failed after {attempts} attempts: {last_error}") from last_error
        src = _safe_file_path(text)
        if src and src.is_file() and src.resolve(strict=False) != target.resolve(strict=False):
            if require_pdf:
                # Validate the copied bytes, not only the earlier source read.
                tmp = target.with_name(target.name + ".download")
                try:
                    shutil.copy2(src, tmp)
                    with tmp.open("rb") as stream:
                        if not _has_pdf_header(stream.read(4)):
                            raise ValueError("registration PDF does not look like a PDF")
                    tmp.replace(target)
                finally:
                    tmp.unlink(missing_ok=True)
            else:
                shutil.copy2(src, target)
            return True
        return False

    def relate_existing(self, paper_id: str, payload: dict, actor: str) -> dict:
        """Merge associations of a registered Paper without claiming disk content."""
        from .resources.directory_registrations import directory_writer
        with directory_writer('paper') as writer:
            if writer.get('paper', paper_id) is None:
                raise KeyError(paper_id)
            found = self.manifest_for_paper_id(paper_id)
            if found is None:
                raise FileNotFoundError('Registered Paper manifest is unavailable')
            path, manifest = found
            paper = _clean_paper({**payload, 'id': paper_id}, actor=actor)
            manifest['relations'] = _merge_paper_relations(manifest.get('relations') or {}, paper)
            manifest['tags'] = _merge_project_tags(manifest.get('tags') or [], paper)
            manifest['source'] = {**(manifest.get('source') or {}), 'updated_at': _now()}
            _write_json(path, manifest)
            self.rebuild_fingerprint_index()
            return self.manifest_to_paper(manifest, path)

    def has_creation_operation(self, payload, actor, operation_id):
        from .resources.registration import ResourceRegistrationStore
        return ResourceRegistrationStore().existing(
            'paper', payload, actor=actor, operation_id=operation_id) is not None

    def register(self, payload: dict, actor: str, *, operation_id: str = '') -> dict:
        from .resources.creation import Creation, creation_operation_id
        from .resources.registration import ResourceRegistrationStore
        _validate_local_pdf_source(str(payload.get("pdf_path") or ""))
        operation_id = creation_operation_id(operation_id)
        clean_payload = dict(payload)
        if not str(clean_payload.get("id") or "").strip():
            from uuid import uuid4
            clean_payload['id'], _ = ResourceRegistrationStore().reserve(
                'paper', payload, actor=actor, operation_id=operation_id,
                allocate=lambda: 'paper_' + uuid4().hex[:12])
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
        from .resources.directory_registrations import register_owned, directory_writer
        from .resources.registry_store import get_registry_store
        from management_common.instance_locations import located_path
        creation = Creation(paper['id'], actor=actor, payload=payload, operation_id=operation_id)
        with creation.prepare([(paper_dir, 'directory')]):
            if creation.completed:
                previous = get_registry_store().get('paper', paper['id'])
                if previous is None:
                    raise KeyError(paper['id'])
                paper_dir = Path(located_path(previous['directories'][0]['path']))
            else:
                if get_registry_store().get('paper', paper['id']) is not None:
                    raise FileExistsError('Paper identity is already registered: ' + paper['id'])
                creation.publish(lambda index, stage: self._initialize_paper_package(paper, payload, stage, creation.paths[0]))
                paper_dir, = creation.paths
                with directory_writer('paper') as writer, creation.registration(writer.connection):
                    register_owned('paper', paper['id'], [paper_dir], connection=writer.connection)
                    from management_portal import permission_system
                    permission_system.sync_resource({**paper, 'resource_id': paper['id']},
                                                    resource_type='paper', connection=writer.connection)
            manifest = self.read_manifest(paper_dir / 'manifest.json')
            if not manifest or manifest.get('paper_id') != paper['id']:
                raise ValueError('Registered Paper manifest is missing or has a different identity')
            self.rebuild_fingerprint_index()
            return self.manifest_to_paper(manifest, paper_dir / 'manifest.json')

    def _initialize_paper_package(self, paper, payload, paper_dir, target):
        paper = dict(paper)
        pdf_source = str(payload.get("pdf_path") or "")
        pdf_error = ""
        try:
            self.ingest_file(pdf_source, paper_dir / "paper.pdf", require_pdf=True)
        except Exception as exc:
            if not _is_url(pdf_source):
                raise
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
        for subdir in ('text', 'reviews', 'notes', 'refs', 'figures', 'runs'):
            (paper_dir / subdir).mkdir()
        manifest = self.manifest_from_paper(paper, paper_dir)
        manifest['root'] = str(target)
        _write_json(paper_dir / 'manifest.json', manifest)

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

    def delete_paper(self, paper_id: str, *, actor: str = '', projection_ids=()) -> dict | None:
        found = self.manifest_for_paper_id(paper_id)
        if not found:
            return None
        manifest_path, manifest = found
        paper_dir = self.paper_dir_from_manifest(manifest, manifest_path)
        from .resources.directory_registrations import unregister_owned
        from .paper_reader_service import production_service
        result = production_service().delete_if_used(paper_id,
            lambda: unregister_owned('paper', paper_id, actor=actor, projection_ids=projection_ids))
        self.rebuild_fingerprint_index()
        return {
            "paper_id": str(manifest.get("paper_id") or paper_id),
            "title": str(manifest.get("title") or ""),
            "deleted_path": str(paper_dir),
            **result,
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
    except (OSError, ValueError):  # missing or unreadable file, or not JSON
        return default


def _write_json(path: Path, payload) -> None:
    _ensure_data_root()
    atomic_replace_json(path, payload, indent=2, sort_keys=False)


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
        with path.open(encoding="utf-8", errors="replace") as stream:
            return stream.read(max(0, limit))
    except OSError:
        return ""


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


def _all_papers() -> list[dict]:
    return PAPER_STORE.all_papers()


def _refs_for_paper(paper: dict) -> list[dict]:
    refs_path = _paper_store_file(paper, "refs")
    refs = _load_json(refs_path, []) if refs_path else []
    return [item for item in refs if isinstance(item, dict)] if isinstance(refs, list) else []


def _paper_job_store_lock(path: Path):
    return exclusive_file_lock(
        path.with_suffix(path.suffix + ".lock")
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


def _validate_local_pdf_source(source: str) -> None:
    """Reject invalid local input before reserving a Paper creation identity."""
    source = source.strip()
    if not source or _is_url(source):
        return
    path = _safe_file_path(source)
    if not path or not path.is_file():
        raise ValueError("registration PDF is unavailable; upload the PDF again")
    try:
        with path.open("rb") as stream:
            header = stream.read(4)
    except OSError as exc:
        raise ValueError("registration PDF is unreadable; upload the PDF again") from exc
    if not _has_pdf_header(header):
        raise ValueError("registration PDF does not look like a PDF")


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
        from management_common.file_retention import cache_file
        with cache_file(path, group='paper_pdf'):
            if not path.is_file() or path.stat().st_size < 20_000:
                req = urllib.request.Request(pdf_path, headers={"User-Agent": "TFIPortalPaperDeepRead/0.1"})
                with urllib.request.urlopen(req, timeout=270) as response:
                    data = response.read()
                if not _has_pdf_header(data):
                    raise RuntimeError("remote PDF did not return PDF bytes")
                path.write_bytes(data)
        return path
    path = _safe_file_path(pdf_path)
    if not path or not path.is_file():
        raise FileNotFoundError("PDF not found")
    return path


def _extract_pdf_text(pdf_path: Path, *, paper_id: str) -> str:
    paper = PAPER_STORE.paper_by_id(paper_id, allow_scan=False)
    text_path = _paper_store_file(paper, "pdf_text_clean") if paper else None
    if not text_path:
        PDF_TEXT_ROOT.mkdir(parents=True, exist_ok=True)
        text_path = PDF_TEXT_ROOT / f"{_slugify(paper_id)}.txt"
    return _extract_pdf_text_file(pdf_path, cache_path=text_path, owner_id=paper_id)


def _extract_pdf_text_raw(pdf_path: Path, *, paper_id: str) -> str:
    paper = PAPER_STORE.paper_by_id(paper_id, allow_scan=False)
    text_path = _paper_store_file(paper, "pdf_text_raw") if paper else None
    if not text_path:
        PDF_TEXT_ROOT.mkdir(parents=True, exist_ok=True)
        text_path = PDF_TEXT_ROOT / f"{_slugify(paper_id)}.raw.txt"
    return _extract_pdf_text_file(pdf_path, cache_path=text_path, owner_id=paper_id, raw=True)


def _summary_text(paper: dict, limit: int = 2000) -> str:
    path = _paper_store_file(paper, "summary")
    return _read_text_preview(path, limit=limit).strip()


def _analysis_text(paper: dict, limit: int = 80_000) -> str:
    path = _safe_file_path(str(paper.get("analysis_path") or ""))
    text = _read_text_preview(path, limit=limit)
    if text:
        return text
    return str(paper.get("abstract") or "")


def _paper_by_id(paper_id: str) -> dict | None:
    return PAPER_STORE.paper_by_id(str(paper_id or ""))


def _pdf_text_for_paper(paper: dict) -> str:
    try:
        return _extract_pdf_text(_local_pdf_for_paper(paper), paper_id=str(paper.get("id") or ""))
    except Exception:
        paper_id = paper.get("id") if isinstance(paper, dict) else None
        LOGGER.warning("PDF text for paper %s is unavailable", paper_id, exc_info=True)
        return ""


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


def _paper_root(paper: dict) -> Path | None:
    root = str((paper or {}).get("_store_root") or "").strip()
    return Path(root).resolve(strict=False) if root else None
