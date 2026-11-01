from __future__ import annotations

import os
import re
import secrets
import unicodedata
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

import yaml

from management_portal.shared_state import exclusive_file_lock

from .common import canonical_record
from ..directory_registrations import directory_writer, register_owned, unregister_owned, is_deleted
from management_common.directory_cleanup_store import get_directory_cleanup_store

if TYPE_CHECKING:
    from ..service import ResourceService


MAX_DOCUMENT_CHARS = 2_000_000


def install_resolver(service: "ResourceService") -> None:
    from .knowledge import install_resolver as install_knowledge_resolver
    install_knowledge_resolver(service, resource_types=("experiment",))


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")


def _text(value: object, limit: int = 0) -> str:
    text = str(value or "").strip()
    if limit and len(text) > limit:
        raise ValueError(f"value exceeds {limit} characters")
    if any(ord(char) < 32 and char not in {"\t"} for char in text):
        raise ValueError("control characters are not allowed")
    return text


def _importance(value: object) -> int:
    if type(value) is not int:
        raise ValueError("experiment importance must be an integer")
    if not 0 <= value <= 10:
        raise ValueError("experiment importance must be between 0 and 10")
    return value


def _project_roots() -> list[tuple[str, Path]]:
    from management_portal import project_registry

    return project_registry.project_roots()


def _project_root(project_id: str) -> Path:
    matches = [
        root
        for candidate_id, root in _project_roots()
        if candidate_id == project_id
    ]
    if not matches:
        raise KeyError(f"Project not found: {project_id}")
    if len(matches) != 1:
        raise ValueError(f"Project id is ambiguous: {project_id}")
    root = matches[0].resolve(strict=False)
    if not root.is_dir():
        raise FileNotFoundError(f"Project directory not found: {root}")
    return root


def _experiment_root(project_root: Path) -> Path:
    return project_root / "knowledge_base" / "experiment"


def _slug(value: object, title: str) -> str:
    requested = _text(value, 80)
    if requested:
        if not SLUG_RE.fullmatch(requested):
            raise ValueError(
                "experiment slug must use lowercase letters, digits, _ or -"
            )
        return requested
    normalized = unicodedata.normalize("NFKD", title).encode(
        "ascii",
        "ignore",
    ).decode("ascii").lower()
    generated = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")[:80]
    return generated if SLUG_RE.fullmatch(generated) else "experiment"


def _split_document(text: str) -> tuple[dict[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = next(
        (
            index
            for index, line in enumerate(lines[1:400], start=1)
            if line.strip() == "---"
        ),
        None,
    )
    if end is None:
        return {}, text
    metadata = yaml.load(
        "\n".join(lines[1:end]),
        Loader=yaml.BaseLoader,
    ) or {}
    if not isinstance(metadata, dict):
        raise ValueError("experiment front matter must be an object")
    return dict(metadata), "\n".join(lines[end + 1 :]).lstrip("\n")


def _render_document(metadata: dict[str, Any], body: str) -> str:
    front_matter = yaml.safe_dump(
        metadata,
        allow_unicode=True,
        sort_keys=False,
    ).strip()
    return f"---\n{front_matter}\n---\n\n{body.rstrip()}\n"


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o7777 if path.exists() else 0o664
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    try:
        temporary.write_text(content, encoding="utf-8")
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _documents() -> Iterable[tuple[str, Path, dict[str, Any], str]]:
    for project_id, project_root in _project_roots():
        root = _experiment_root(project_root)
        if not root.is_dir():
            continue
        try:
            entries = sorted(root.iterdir(), key=lambda path: path.name)
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if get_directory_cleanup_store().pending_at(entry):
                continue
            candidates = (
                [entry]
                if entry.is_file() and entry.suffix.lower() == ".md"
                else sorted(entry.glob("*.md")) if entry.is_dir() else []
            )
            for path in candidates:
                try:
                    metadata, body = _split_document(
                        path.read_text(encoding="utf-8")
                    )
                except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError):
                    continue
                if _text(metadata.get("type")).lower() not in {"", "experiment"}:
                    continue
                resource_id = _text(metadata.get("kb_id"))
                if is_deleted(resource_id):
                    continue
                if re.fullmatch(r"experiment_[0-9a-f]{12}", resource_id):
                    yield project_id, path, metadata, body
                    break


def _record(
    project_id: str,
    path: Path,
    metadata: dict[str, Any],
    body: str,
    *,
    created: bool,
) -> dict[str, Any]:
    resource_id = _text(metadata.get("kb_id"))
    title = ""
    for line in body.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    try:
        importance = int(str(metadata.get("importance") or 0))
    except (TypeError, ValueError):
        importance = 0
    return canonical_record(
        resource_id,
        {
            "resource_type": "experiment",
            "project_ids": [project_id],
            "title": title or path.parent.name,
            "path": str(path),
            "content_root": str(path.parent),
            "status": _text(metadata.get("status")),
            "importance": importance,
            "created": created,
        },
    )


def _by_resource_id(
    resource_id: str,
) -> tuple[str, Path, dict[str, Any], str]:
    matches = [
        item
        for item in _documents()
        if _text(item[2].get("kb_id")) == resource_id
    ]
    if not matches:
        raise KeyError(f"Experiment not found: {resource_id}")
    if len(matches) > 1:
        raise RuntimeError(f"duplicate Experiment resource: {resource_id}")
    return matches[0]


def register_experiment(
    service: "ResourceService",
    payload: dict[str, Any],
    *,
    actor: str = "",
    resource_id: str = "",
    operation_id: str = "",
) -> dict[str, Any]:
    project_id = _text(payload.get("project_id"), 80)
    title = _text(payload.get("title"), 200)
    if not project_id:
        raise ValueError("experiment project_id required")
    if not title:
        raise ValueError("experiment title required")
    service.validate_id(resource_id, "experiment")
    project_root = _project_root(project_id)
    root = _experiment_root(project_root)
    entry_root = root / f"{_slug(payload.get('slug'), title)}__{resource_id}"
    path = entry_root / "experiment.md"
    from ..creation import Creation
    from ..registry_store import get_registry_store
    creation = Creation(resource_id, actor=actor, payload=payload, operation_id=operation_id)
    with creation.prepare([(entry_root, 'directory')]):
        if creation.completed:
            previous = get_registry_store().get('knowledge', resource_id)
            if previous is None:
                raise KeyError(resource_id)
            from management_common.instance_locations import located_path
            path = Path(located_path(previous['directories'][0]['path'])) / 'experiment.md'
            metadata, body = _split_document(path.read_text(encoding='utf-8'))
            return _record(project_id, path, metadata, body, created=False)
        if get_registry_store().get('knowledge', resource_id) is not None:
            raise FileExistsError('Experiment identity is already registered: ' + resource_id)
        status = _text(payload.get("status") or "planned", 80)
        importance = _importance(payload.get("importance", 5))
        body_text = str(
            payload.get("body")
            if payload.get("body") is not None
            else payload.get("summary") or ""
        )
        if len(body_text) > MAX_DOCUMENT_CHARS:
            raise ValueError("experiment body is too large")
        body = f"# {title}\n\n{body_text.strip()}".rstrip()
        metadata = {
            "kb_id": resource_id,
            "type": "experiment",
            "created_at": date.today().isoformat(),
            "status": status,
            "importance": importance,
        }
        creation.publish(lambda index, stage: _write_atomic(stage / 'experiment.md', _render_document(metadata, body)))
        entry_root, = creation.paths
        path = entry_root / 'experiment.md'
        with directory_writer('knowledge') as writer, creation.registration(writer.connection):
            register_owned('knowledge', resource_id, [entry_root], connection=writer.connection)
            from management_portal import permission_system
            permission_system.sync_resource({'resource_id': resource_id, 'project_ids': [project_id]},
                                            resource_type='experiment', connection=writer.connection)
        return _record(
            project_id,
            path,
            metadata,
            body,
            created=True,
        )


def _mutation_paths(project_id: str, path: Path) -> tuple[Path, Path]:
    knowledge_root = _project_root(project_id) / "knowledge_base"
    experiment_root = (knowledge_root / "experiment").resolve()
    entry_root = path.parent.resolve()
    if entry_root.parent != experiment_root or not path.resolve().is_relative_to(entry_root):
        raise ValueError("experiment mutation requires a contained experiment directory")
    return entry_root, knowledge_root / ".experiment-resource.lock"


def update_experiment(
    resource_id: str,
    payload: dict[str, Any],
    *,
    actor: str = "",
) -> dict[str, Any]:
    del actor
    unsupported = set(payload) - {"status", "importance", "body", "summary"}
    if unsupported:
        raise ValueError(
            "unsupported experiment update fields: "
            + ", ".join(sorted(unsupported))
        )
    if not payload:
        raise ValueError("experiment update requires at least one field")
    project_id, path, metadata, body = _by_resource_id(resource_id)
    _entry_root, lock_path = _mutation_paths(project_id, path)
    with exclusive_file_lock(lock_path):
        project_id, path, metadata, body = _by_resource_id(resource_id)
        _mutation_paths(project_id, path)
        previous_content = path.read_text(encoding="utf-8")
        if "status" in payload:
            metadata["status"] = _text(payload.get("status"), 80)
        if "importance" in payload:
            metadata["importance"] = _importance(payload["importance"])
        if "body" in payload or "summary" in payload:
            next_body = str(
                payload.get("body")
                if "body" in payload
                else payload.get("summary") or ""
            )
            if len(next_body) > MAX_DOCUMENT_CHARS:
                raise ValueError("experiment body is too large")
            heading = next(
                (line for line in body.splitlines() if line.startswith("# ")),
                f"# {path.parent.name}",
            )
            body = f"{heading}\n\n{next_body.strip()}".rstrip()
        stored_content = _render_document(metadata, body)
        _write_atomic(path, stored_content)
        from ..content_changes import record_text_change

        record_text_change(resource_id, path, previous_content, stored_content)
    return _record(
        project_id,
        path,
        metadata,
        body,
        created=False,
    )


def unregister_experiment(
    resource_id: str,
    *,
    actor: str = "",
) -> dict[str, Any]:
    project_id, path, metadata, body = _by_resource_id(resource_id)
    _entry_root, lock_path = _mutation_paths(project_id, path)
    with exclusive_file_lock(lock_path):
        project_id, path, metadata, body = _by_resource_id(resource_id)
        entry_root, _lock_path = _mutation_paths(project_id, path)
        record = _record(
            project_id,
            path,
            metadata,
            body,
            created=False,
        )
        result = unregister_owned('knowledge', resource_id, actor=actor)
    return {
        **record,
        **result,
    }


def install_mutation_provider(service: "ResourceService") -> None:
    service.register_mutation_provider(
        "experiment",
        register=lambda payload, **context: register_experiment(
            service,
            payload,
            actor=str(context.get("actor") or ""),
            resource_id=str(context.get("resource_id") or ""),
        ),
        update=lambda resource_id, payload, **context: update_experiment(
            resource_id,
            payload,
            actor=str(context.get("actor") or ""),
        ),
        unregister=lambda resource_id, **context: unregister_experiment(
            resource_id,
            actor=str(context.get("actor") or ""),
        ),
        service_managed_registration=True,
    )
