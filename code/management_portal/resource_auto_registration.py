from __future__ import annotations
from management_common.product_environment import read_product_env
from management_common.portal_paths import path as portal_path

from management_common.file_lock import try_lock_descriptor
from management_common.clock import utc_now_iso
import logging
import threading
import time
from pathlib import Path
from typing import Any, Iterable


from management_portal import portal_features, project_registry, resource_identity_backend
from management_portal.resources.registry_store import get_registry_store
from management_portal.resources import ResourceService, get_resource_service


LOGGER = logging.getLogger(__name__)
IGNORE_COLLECTION = "auto_registration_ignore"
IGNORE_SCHEMA = "tfi.resource-auto-ignores.v1"
DEFAULT_INTERVAL_SECONDS = 24 * 60 * 60
DEFAULT_INITIAL_DELAY_SECONDS = 60
DEFAULT_RUN_STATE_PATH = Path(
    read_product_env(
        "PORTAL_RESOURCE_AUTO_REGISTER_STATE_PATH",
        str(portal_path("staging_root") / "shared" / "portal_runtime" / "resource-auto-registration.last-run"),
    )
).expanduser()


def _normalized_path(value: str | Path) -> str:
    return str(Path(value).expanduser().resolve(strict=False))


def _ignore_id(resource_type: str, path: str) -> str:
    return f"{resource_type}:{path}"


def suppressed_paths(resource_type: str) -> set[str]:
    """Paths whose resources a user unregistered; scans skip them."""
    return {
        _normalized_path(entry.get("path"))
        for entry in get_registry_store().documents(IGNORE_COLLECTION).values()
        if str(entry.get("resource_type") or "") == resource_type
        and str(entry.get("path") or "").strip()
    }


def suppress_paths(
    resource_type: str,
    resource_id: str,
    paths: Iterable[str | Path],
) -> None:
    normalized = {_normalized_path(value) for value in paths if str(value).strip()}
    if not normalized:
        return
    with get_registry_store().writer(IGNORE_COLLECTION) as writer:
        for path in sorted(normalized):
            writer.put(IGNORE_COLLECTION, _ignore_id(resource_type, path), {
                "schema": IGNORE_SCHEMA,
                "resource_type": resource_type,
                "path": path,
                "resource_id": resource_id,
                "ignored_at": utc_now_iso(),
            })


def clear_suppressed_path(resource_type: str, path: str | Path) -> None:
    with get_registry_store().writer(IGNORE_COLLECTION) as writer:
        writer.delete(IGNORE_COLLECTION, _ignore_id(resource_type, _normalized_path(path)))


def prune_missing_suppressions() -> list[str]:
    """A suppression only guards a directory that still exists; a later directory at the path is new."""
    stale = sorted(
        document_id
        for document_id, entry in get_registry_store().documents(IGNORE_COLLECTION).items()
        if not str(entry.get("path") or "").strip() or not Path(str(entry["path"])).exists()
    )
    if stale:
        with get_registry_store().writer(IGNORE_COLLECTION) as writer:
            for document_id in stale:
                writer.delete(IGNORE_COLLECTION, document_id)
    return stale


def _contained(path: str | Path, roots: Iterable[str]) -> bool:
    candidate = Path(path).resolve(strict=False)
    for raw_root in roots:
        try:
            candidate.relative_to(Path(raw_root))
        except ValueError:
            continue
        return True
    return False


class ResourceAutoRegistrar:
    def __init__(
        self,
        *,
        service: ResourceService | None = None,
    ) -> None:
        self.service = service or get_resource_service()

    @staticmethod
    def _resource_error(
        errors: list[dict[str, str]],
        exc: Exception,
        *,
        resource_type: str,
        resource_id: str = "",
        path: str = "",
        operation: str,
    ) -> None:
        errors.append(
            {
                "resource_type": resource_type,
                "resource_id": str(resource_id or "").strip(),
                "path": str(path or "").strip(),
                "operation": operation,
                "error": str(exc)[:1000],
            }
        )

    def _registry_records(
        self,
        errors: list[dict[str, str]],
        resource_type: str,
        collection: str,
        validate,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for document_id, document in get_registry_store().documents(collection).items():
            try:
                records.append(validate(document_id, document))
            except Exception as exc:
                self._resource_error(
                    errors,
                    exc,
                    resource_type=resource_type,
                    resource_id=document_id,
                    operation="read",
                )
        return records

    def _repo_records(self, errors: list[dict[str, str]]) -> list[dict[str, Any]]:
        return self._registry_records(
            errors, "repo", resource_identity_backend.REPO_COLLECTION,
            resource_identity_backend._validated_repo,
        )

    def reconcile(self) -> dict[str, Any]:
        article_source = portal_features.contribution("resource.article_registration")
        errors: list[dict[str, str]] = []
        project_roots = [
            path
            for _project_id, path in project_registry.project_roots()
            if path.is_dir()
        ]
        # Without any visible Project root the storage itself may be missing.
        pruned = prune_missing_suppressions() if project_roots else []
        articles: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        registered_articles: list[dict[str, Any]] = []
        ignored_articles: set[str] = set()
        if article_source is not None:
            articles, skipped = article_source.discover_roots(project_roots)
            registered_articles, article_errors = article_source.records()
            errors.extend(article_errors)
            ignored_articles = suppressed_paths("article")
        article_paths = {
            _normalized_path(item["path"]) for item in articles
        } | {
            _normalized_path(item["path"])
            for item in skipped
            if item.get("path")
        } | ignored_articles
        for record in registered_articles:
            article_paths.update(
                _normalized_path(root["path"])
                for root in record.get("roots") or []
                if isinstance(root, dict) and root.get("path")
            )

        removed: list[str] = []
        registered_repos = self._repo_records(errors)
        retained_repos: list[dict[str, Any]] = []
        for record in registered_repos:
            paths: list[str] = []
            try:
                paths = resource_identity_backend.repo_checkout_paths(record)
                unavailable = not resource_identity_backend.repo_content_available(record)
            except Exception as exc:
                self._resource_error(
                    errors,
                    exc,
                    resource_type="repo",
                    resource_id=str(record.get("id") or ""),
                    path=str(paths[0] if paths else ""),
                    operation="validate",
                )
                retained_repos.append(record)
                continue
            article_only = bool(paths) and all(
                _contained(path, article_paths) for path in paths
            )
            if unavailable or article_only:
                try:
                    self.service.unregister(
                        str(record["id"]),
                        actor="resource-auto-registration",
                    )
                    for path in paths:
                        clear_suppressed_path("repo", path)
                    removed.append(str(record["id"]))
                except Exception as exc:
                    self._resource_error(
                        errors,
                        exc,
                        resource_type="repo",
                        resource_id=str(record.get("id") or ""),
                        path=str(paths[0] if paths else ""),
                        operation="unregister",
                    )
                    retained_repos.append(record)
            else:
                retained_repos.append(record)

        registered: list[str] = []
        ignored_repos = suppressed_paths("repo")
        checkouts, invalid_markers = (
            resource_identity_backend.discover_git_checkouts(
                project_roots,
                excluded_roots=article_paths,
            )
            if project_roots else ([], [])
        )
        known_repo_paths = {
            _normalized_path(path)
            for record in retained_repos
            for path in resource_identity_backend.repo_checkout_paths(record)
        }
        for checkout in checkouts:
            path = _normalized_path(checkout["path"])
            if path in known_repo_paths or path in ignored_repos:
                continue
            try:
                record = self.service.register(
                    "repo",
                    {"path": path},
                    actor="resource-auto-registration",
                    automatic=True,
                )
                registered.append(str(record["resource_id"]))
            except Exception as exc:
                self._resource_error(
                    errors,
                    exc,
                    resource_type="repo",
                    path=path,
                    operation="register",
                )

        # Article Git roots are Article storage, not Repo resources.
        known_article_paths = {
            _normalized_path(root["path"])
            for record in registered_articles
            for root in record.get("roots") or []
            if isinstance(root, dict) and root.get("path")
        }
        for article in articles:
            path = _normalized_path(article["path"])
            if path in known_article_paths or path in ignored_articles:
                continue
            try:
                record = self.service.register(
                    "article",
                    {"path": path},
                    actor="resource-auto-registration",
                    automatic=True,
                )
                registered.append(str(record["resource_id"]))
            except Exception as exc:
                self._resource_error(
                    errors,
                    exc,
                    resource_type="article",
                    path=path,
                    operation="register",
                )

        return {
            "project_count": len(project_roots),
            "registered": registered,
            "removed": removed,
            "pruned_suppressions": pruned,
            "skipped_articles": skipped,
            "invalid_git_markers": invalid_markers,
            "resource_errors": errors,
        }


def reconcile_if_due(
    registrar: ResourceAutoRegistrar,
    *,
    state_path: str | Path = DEFAULT_RUN_STATE_PATH,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    now=time.time,
) -> dict[str, Any]:
    path = Path(state_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    current = float(now())
    with path.open("a+", encoding="utf-8") as stream:
        if not try_lock_descriptor(stream.fileno()):
            return {"ran": False, "reason": "locked", "retry_after": 60}
        stream.seek(0)
        try:
            last_run = float(stream.read().strip() or 0)
        except ValueError:
            last_run = 0
        interval = max(60.0, float(interval_seconds))
        elapsed = max(0.0, current - last_run)
        if last_run > 0 and elapsed < interval:
            return {
                "ran": False,
                "reason": "recent",
                "retry_after": max(60.0, interval - elapsed),
            }
        report = registrar.reconcile()
        resource_errors = report.get("resource_errors") or []
        if not resource_errors:
            stream.seek(0)
            stream.truncate()
            stream.write(f"{current:.6f}\n")
            stream.flush()
        return {
            "ran": True,
            "retry_after": (
                min(interval, 15 * 60)
                if resource_errors
                else interval
            ),
            "report": report,
        }


def start_auto_registration_worker(
    *,
    service: ResourceService | None = None,
    stop_event: threading.Event | None = None,
) -> threading.Thread | None:
    if read_product_env("PORTAL_RESOURCE_AUTO_REGISTER", "1").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return None
    interval = max(
        60,
        int(
            read_product_env(
                "PORTAL_RESOURCE_AUTO_REGISTER_INTERVAL_SECONDS",
                str(DEFAULT_INTERVAL_SECONDS),
            )
        ),
    )
    initial_delay = max(
        0,
        int(
            read_product_env(
                "PORTAL_RESOURCE_AUTO_REGISTER_INITIAL_DELAY_SECONDS",
                str(DEFAULT_INITIAL_DELAY_SECONDS),
            )
        ),
    )

    stopped = stop_event or threading.Event()

    def run() -> None:
        if initial_delay and stopped.wait(initial_delay):
            return
        registrar = ResourceAutoRegistrar(service=service)
        while not stopped.is_set():
            wait_seconds = float(interval)
            try:
                result = reconcile_if_due(
                    registrar,
                    interval_seconds=interval,
                )
                wait_seconds = float(result.get("retry_after") or interval)
            except Exception:
                LOGGER.exception("resource auto-registration pass failed")
                wait_seconds = min(float(interval), 15 * 60)
            if stopped.wait(max(60.0, wait_seconds)):
                return

    thread = threading.Thread(
        target=run,
        name="resource-auto-registration",
        daemon=True,
    )
    thread.start()
    return thread
