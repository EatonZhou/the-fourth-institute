"""Generated one-line Repo descriptions: the evidence read from each checkout and the batches."""

from __future__ import annotations

import hashlib
import re
import threading
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

from management_common.text_values import stripped_text
from management_portal import resource_identity_backend
from management_portal.repo_workspace_ai import (
    RepoDescriptionGenerationError, _generate_repo_description_batch, _normalize_description_generator_result,
)
from management_portal.repo_workspace_checkout import _checkout, _target
from management_portal.repo_workspace_git import GitOperationError, _git


MAX_DESCRIPTION_CONTEXT_BYTES = 8 * 1024
MAX_DESCRIPTION_BATCH_SIZE = 12
MAX_DESCRIPTION_REQUEST_REPOS = 120

_DESCRIPTION_CONTEXT_FILENAMES = (
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "setup.py",
    "setup.cfg",
    "composer.json",
    "Gemfile",
    "CMakeLists.txt",
    "package.xml",
    "manifest.xml",
    "agent.yaml",
)

_DESCRIPTION_GENERATION_LOCK = threading.RLock()


def _description_context_files(
    checkout: Path,
    repo_name: str,
) -> list[Path]:
    try:
        root_files = [
            item
            for item in checkout.iterdir()
            if item.is_file() and not item.is_symlink()
        ]
    except OSError:
        return []
    readmes = sorted(
        (
            item
            for item in root_files
            if item.name.casefold().startswith("readme")
        ),
        key=lambda item: (
            item.name.casefold() != "readme.md",
            item.name.casefold(),
        ),
    )
    if not readmes:
        tracked_readmes = _git(
            checkout,
            "ls-files",
            "--",
            ":(icase,glob)**/README*",
            allowed_returncodes=(0, 128),
        ).splitlines()
        name_tokens = {
            token
            for token in re.split(r"[^a-z0-9]+", repo_name.casefold())
            if len(token) >= 4
        }
        nested: list[tuple[int, int, str, Path]] = []
        for raw_relative in tracked_readmes:
            relative = PurePosixPath(raw_relative)
            if (
                len(relative.parts) > 3
                or any(part in resource_identity_backend.PRUNED_DIR_NAMES for part in relative.parts)
                or not relative.name.casefold().startswith("readme")
            ):
                continue
            try:
                path = _target(checkout, relative.as_posix())
            except ValueError:
                continue
            if not path.is_file() or path.is_symlink():
                continue
            relevance = sum(
                token in relative.as_posix().casefold()
                for token in name_tokens
            )
            nested.append(
                (-relevance, len(relative.parts), relative.as_posix().casefold(), path)
            )
        readmes = [item[3] for item in sorted(nested)[:2]]
    by_name = {item.name: item for item in root_files}
    selected = [*readmes[:2]]
    selected.extend(
        by_name[name]
        for name in _DESCRIPTION_CONTEXT_FILENAMES
        if name in by_name
    )
    output: list[Path] = []
    seen: set[Path] = set()
    for path in selected:
        if path in seen:
            continue
        seen.add(path)
        output.append(path)
    return output


def _repo_description_context(
    repo_id: str,
) -> dict[str, Any]:
    record, checkout = _checkout(repo_id)
    repo_name = stripped_text(record.get("name")) or checkout.name
    evidence_sections: list[str] = []
    signature = hashlib.sha256()
    signature.update(repo_id.encode("utf-8"))
    signature.update(b"\0")
    signature.update(stripped_text(record.get("name")).encode("utf-8", errors="replace"))
    signature.update(b"\0")
    for project_id in record.get("project_ids") or []:
        signature.update(stripped_text(project_id).encode("utf-8", errors="replace"))
        signature.update(b"\0")

    remaining = MAX_DESCRIPTION_CONTEXT_BYTES
    for path in _description_context_files(checkout, repo_name):
        if remaining <= 0:
            break
        try:
            with path.open("rb") as stream:
                raw = stream.read(min(remaining, 5 * 1024))
        except OSError:
            continue
        if not raw or b"\x00" in raw:
            continue
        try:
            evidence_name = path.relative_to(checkout).as_posix()
        except ValueError:
            evidence_name = path.name
        signature.update(evidence_name.encode("utf-8", errors="replace"))
        signature.update(b"\0")
        signature.update(raw)
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        section = f"### {evidence_name}\n{text}"
        evidence_sections.append(section)
        remaining -= len(section.encode("utf-8", errors="replace"))

    top_level = _git(
        checkout,
        "ls-tree",
        "--name-only",
        "HEAD",
        allowed_returncodes=(0, 128),
    ).splitlines()
    top_level = sorted(
        {value for value in top_level if value and not value.startswith(".")},
        key=str.casefold,
    )[:80]
    latest_subject = _git(
        checkout,
        "log",
        "-1",
        "--format=%s",
        allowed_returncodes=(0, 128),
    )
    location_hint = "/".join(checkout.parts[-4:-1])
    structural = (
        f"Parent context: {location_hint or '(none)'}\n"
        f"Top-level tracked entries: {', '.join(top_level) or '(none)'}\n"
        f"Latest commit subject: {latest_subject or '(none)'}"
    )
    signature.update(structural.encode("utf-8", errors="replace"))
    evidence_sections.append(f"### Repository structure\n{structural}")
    return {
        "id": repo_id,
        "name": repo_name,
        "project_ids": [
            stripped_text(value)
            for value in record.get("project_ids") or []
            if stripped_text(value)
        ],
        "evidence": "\n\n".join(evidence_sections),
        "source_signature": signature.hexdigest(),
    }


def generate_repo_descriptions(
    repo_ids: Iterable[str] = (),
    *,
    force: bool = False,
    actor: str = "",
    provider_id: str = "",
    description_generator: (
        Callable[[list[dict[str, Any]]], dict[str, Any]] | None
    ) = None,
) -> dict[str, Any]:
    requested = [
        stripped_text(repo_id)
        for repo_id in repo_ids
        if stripped_text(repo_id)
    ]
    requested = list(dict.fromkeys(requested))
    if not requested:
        requested = [
            stripped_text(record.get("id"))
            for record in resource_identity_backend.load_repo_records()
        ]
    if len(requested) > MAX_DESCRIPTION_REQUEST_REPOS:
        raise ValueError(
            f"at most {MAX_DESCRIPTION_REQUEST_REPOS} repos can be described at once"
        )

    generated_records: list[dict[str, Any]] = []
    skipped: list[str] = []
    errors: list[dict[str, str]] = []
    usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    with _DESCRIPTION_GENERATION_LOCK:
        contexts: list[dict[str, Any]] = []
        for repo_id in requested:
            try:
                record = resource_identity_backend.get_repo_record(repo_id)
                if stripped_text(record.get("description")) and not force:
                    skipped.append(repo_id)
                    continue
                contexts.append(_repo_description_context(repo_id))
            except (KeyError, FileNotFoundError, ValueError, GitOperationError) as exc:
                errors.append({"id": repo_id, "error": str(exc)})

        for offset in range(0, len(contexts), MAX_DESCRIPTION_BATCH_SIZE):
            batch = contexts[offset : offset + MAX_DESCRIPTION_BATCH_SIZE]
            try:
                raw_result = (
                    description_generator(batch)
                    if description_generator is not None
                    else _generate_repo_description_batch(
                        batch,
                        actor=actor,
                        provider_id=provider_id,
                    )
                )
                result = _normalize_description_generator_result(
                    raw_result,
                    batch,
                )
            except RepoDescriptionGenerationError as exc:
                errors.extend(
                    {"id": context["id"], "error": str(exc)}
                    for context in batch
                )
                continue
            for key in usage:
                usage[key] += int((result.get("usage") or {}).get(key) or 0)
            descriptions = result["descriptions"]
            for context in batch:
                repo_id = context["id"]
                description = descriptions.get(repo_id)
                if not description:
                    errors.append(
                        {
                            "id": repo_id,
                            "error": "Kimi 没有返回该 Repo 的简介。",
                        }
                    )
                    continue
                current = resource_identity_backend.get_repo_record(repo_id)
                if stripped_text(current.get("description")) and not force:
                    skipped.append(repo_id)
                    continue
                updated = resource_identity_backend.update_repo_description(
                    repo_id,
                    description,
                    source_signature=context["source_signature"],
                    provider=result["provider"],
                    model=result["model"],
                )
                generated_records.append(
                    {
                        "id": repo_id,
                        "description": stripped_text(updated.get("description")),
                        "provider": result["provider"],
                        "model": result["model"],
                        "source_signature": context["source_signature"],
                    }
                )

    return {
        "schema": "tfi.repo-descriptions.v1",
        "requested_count": len(requested),
        "generated_count": len(generated_records),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "repos": generated_records,
        "skipped": list(dict.fromkeys(skipped)),
        "errors": errors,
        "usage": usage,
    }
