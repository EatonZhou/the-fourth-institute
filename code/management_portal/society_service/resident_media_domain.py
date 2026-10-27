"""Resident domain responsibilities: media."""
from __future__ import annotations

from . import (
    resident_backend,
    resident_domain_common,
    resident_message_contract,
    resident_record_domain,
    workspace_contracts,
)
from .workspace_attachments import authorize_attachment_read
from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from management_common.actor_identity import canonical_actor_id
from management_portal.execution.contracts import (
    InputMedia,
    MAX_INPUT_MEDIA_ITEMS,
    MAX_INPUT_MEDIA_MIME_CHARS,
    MAX_INPUT_MEDIA_NAME_CHARS,
    MAX_INPUT_MEDIA_SOURCE_CHARS,
)
from pathlib import Path
from typing import Any, Iterable
import os
import re
import shutil
import tempfile


RESIDENT_IMAGE_MARKER_RE = re.compile(
    r"\[image:\s*([^\]\r\n]+?)\s*\]",
    re.IGNORECASE,
)


def stage_resident_input_images(
    attachments: list[dict[str, str]], runtime_dir: Path, message_id: str,
) -> list[dict[str, str]]:
    """Keep accepted image inputs independent of mutable composer uploads."""
    staged: list[dict[str, str]] = []
    for index, item in enumerate(attachments):
        if item.get("media_type") != "image" or item.get("source_type") != "local_path":
            staged.append(item)
            continue
        source = Path(item["source"])
        directory = runtime_dir / "input_media" / message_id
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = directory / f"{index}{source.suffix}"
        if target.is_symlink():
            raise PermissionError("Resident input image staging path is a symlink")
        if not target.is_file():
            temporary = None
            try:
                with source.open("rb") as reader, tempfile.NamedTemporaryFile(dir=directory, delete=False) as writer:
                    temporary = Path(writer.name)
                    shutil.copyfileobj(reader, writer)
                    writer.flush()
                    os.fsync(writer.fileno())
                try:
                    os.link(temporary, target)
                except FileExistsError:
                    if target.is_symlink() or not target.is_file():
                        raise
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        staged.append({**item, "source": str(target), "original_source": str(source)})
    return staged


def _cleanup_resident_input_images(runtime_dir: Path, message_id: str) -> None:
    if not resident_message_contract.MESSAGE_IDENTITY_PATTERN.fullmatch(message_id):
        return
    directory = runtime_dir / "input_media" / message_id
    if directory.is_dir() and not directory.is_symlink():
        shutil.rmtree(directory)


def cleanup_expired_input_images(record: dict[str, Any], *, dry_run: bool = False) -> list[str]:
    """Remove terminal input copies after they leave the requeue window."""
    from . import resident_conversation_stream, resident_input_checkpoints, resident_mailbox

    # Requeue uses this same lock: retry checkpoints and terminal states stay valid
    # until their staged copies have been removed.
    with resident_record_domain.state_lock(record["resident_id"]):
        runtime_dir = resident_record_domain.sessions_path(record)
        root = runtime_dir / "input_media"
        if not root.is_dir() or root.is_symlink():
            return []
        retained: set[str] = set()
        for entry in resident_input_checkpoints.checkpoints(record):
            retained.add(str(entry.get("message_id") or ""))
            retained.update(str(info.get("message_id") or "") for info in entry.get("infos") or [])
            if entry.get("kind") == "group":
                retained.update(resident_mailbox.members_of(record, str(entry.get("message_id") or "")))
            for member in (entry.get("item") or {}).get("mailbox_members") or []:
                retained.add(str(member.get("user_message_id") or member.get("message_id") or ""))
        repository = resident_conversation_stream.get_repository()
        removed = []
        for directory in root.iterdir():
            message_id = directory.name
            if message_id in retained or directory.is_symlink() or not directory.is_dir():
                continue
            if not resident_message_contract.MESSAGE_IDENTITY_PATTERN.fullmatch(message_id):
                continue
            try:
                state = repository.input_state(record["resident_id"], message_id)
            except KeyError:
                continue
            if state.get("state") not in resident_input_checkpoints.FINISHED_STATES:
                continue
            if not dry_run:
                _cleanup_resident_input_images(runtime_dir, message_id)
            removed.append(message_id)
        return removed


def _uploaded_input_media(upload_id: str, media_type: str, actor_id: str) -> dict[str, str]:
    from management_portal import permission_system
    from management_portal.resources.isa import read_upload

    meta = read_upload(upload_id)
    if permission_system.owners_only() and str(meta.get("created_by") or "") != actor_id:
        raise PermissionError("只能发送自己上传的附件")
    if not meta.get("ready"):
        raise ValueError("附件还没有上传完成")
    path = Path(str(meta.get("path") or "")).resolve(strict=True)
    max_bytes = (
        workspace_contracts.CHAT_IMAGE_MAX_BYTES
        if media_type == "image"
        else workspace_contracts.CHAT_ATTACHMENT_MAX_BYTES
    )
    size = path.stat().st_size
    if size <= 0 or size > max_bytes:
        raise ValueError("附件大小不符合限制")
    content_type = str(meta.get("media_type") or "application/octet-stream")
    if media_type == "image":
        content_type, _suffix = workspace_contracts.image_type(path.name, content_type)
    media = {
        "media_type": media_type,
        "source_type": "local_path",
        "source": str(path),
        "mime_type": content_type,
        "name": str(meta.get("file_name") or path.name),
    }
    return InputMedia.from_payload(media).to_payload() if media_type == "image" else media


def resident_input_media(
    selection: object,
    *,
    actor: str,
    resident_id: str,
    content: str,
    workspace_roots: Iterable[Path] = (),
) -> list[dict[str, str]]:
    """Validate local attachments before projecting files or native images."""

    actor_id = canonical_actor_id(actor)
    explicit = selection is not None
    if explicit and not isinstance(selection, (list, tuple)):
        raise ValueError("input_media must be an array")
    candidates: list[tuple[object, bool]] = [
        (item, True) for item in (selection or [])
    ]
    if not candidates:
        candidates.extend(
            ({"media_type": "image", "source": match.group(1)}, False)
            for match in RESIDENT_IMAGE_MARKER_RE.finditer(str(content or ""))
        )
    if len(candidates) > MAX_INPUT_MEDIA_ITEMS:
        raise ValueError("input_media contains too many items")

    allowed_workspace_roots: tuple[Path, ...] = tuple(
        root.expanduser().resolve(strict=False)
        for root in workspace_roots
        if root.is_dir()
    )
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw, strict in candidates:
        if not isinstance(raw, dict):
            if strict:
                raise ValueError("input_media item must be an object")
            continue
        declared_types = {
            str(raw.get(field) or "").strip().casefold()
            for field in ("type", "media_type", "kind")
            if str(raw.get(field) or "").strip()
        }
        if len(declared_types) > 1:
            raise ValueError("Resident input attachment has conflicting types")
        media_type = next(iter(declared_types), "image")
        if media_type not in {"image", "file"}:
            if strict:
                raise ValueError("unsupported Resident input attachment type")
            continue
        upload_id = str(raw.get("upload_id") or "").strip()
        if upload_id:
            # An attachment uploaded with CreateUpload: only its uploader may send it.
            media = _uploaded_input_media(upload_id, media_type, actor_id)
            key = (media_type, media["source"])
            if key not in seen:
                seen.add(key)
                normalized.append(media)
            continue
        source = str(
            raw.get("source") or raw.get("path") or raw.get("url") or ""
        ).strip()
        source_type = str(raw.get("source_type") or "").strip().casefold()
        if not source_type:
            source_type = (
                "url"
                if source.casefold().startswith(("https://", "http://"))
                else "local_path"
            )
        if source_type == "url":
            if strict:
                raise ValueError(
                    "Portal Resident input_media requires a local attachment"
                )
            continue
        elif source_type == "local_path":
            try:
                if (
                    not source or len(source) > MAX_INPUT_MEDIA_SOURCE_CHARS
                    or "\x00" in source or not Path(source).is_absolute()
                    or ".." in Path(source).parts
                ):
                    raise ValueError("invalid Resident input attachment path")
                if media_type == "file" and any(char in source for char in "\r\n[]"):
                    raise ValueError("Resident input file path cannot form a file reference")
                path = Path(source).resolve(strict=True)
                upload_root = (
                    workspace_contracts.CHAT_IMAGE_CACHE_ROOT
                    if media_type == "image"
                    else workspace_contracts.CHAT_ATTACHMENT_CACHE_ROOT
                ).resolve(strict=False)
                stat = path.stat()
                if not path.is_file() or stat.st_size <= 0:
                    raise ValueError("Resident input attachment is not a readable file")
                max_bytes = (
                    workspace_contracts.CHAT_IMAGE_MAX_BYTES
                    if media_type == "image"
                    else workspace_contracts.CHAT_ATTACHMENT_MAX_BYTES
                )
                if stat.st_size > max_bytes:
                    raise ValueError("Resident input attachment exceeds the size limit")
                try:
                    relative_path = path.relative_to(upload_root)
                except ValueError:
                    relative_path = None
                workspace_path = any(
                    resident_domain_common.path_is_within(path, root)
                    for root in allowed_workspace_roots
                )
                metadata: dict[str, Any] = {}
                if relative_path is not None:
                    metadata = authorize_attachment_read(path, actor_id, root=upload_root)
                elif not workspace_path:
                    raise PermissionError(
                        "Resident input attachment is outside its upload and workspace roots"
                    )
                if media_type == "image" and metadata:
                    content_type, _suffix = workspace_contracts.image_type(
                        path.name, str(metadata.get("content_type") or ""),
                    )
                elif media_type == "image":
                    content_type, _suffix = workspace_contracts.image_type(path.name, "")
                    requested_type = str(raw.get("mime_type") or "").strip().lower()
                    if requested_type and requested_type != content_type:
                        raise ValueError("Resident input image MIME type does not match")
                else:
                    content_type = str(metadata.get("content_type") or raw.get("mime_type") or "application/octet-stream")
                media = {
                    "media_type": media_type,
                    "source_type": "local_path",
                    "source": str(path),
                    "mime_type": content_type,
                    "name": str(
                        metadata.get("filename")
                        or raw.get("name")
                        or raw.get("filename")
                        or path.name
                    ).strip(),
                }
                if media_type == "image":
                    media = InputMedia.from_payload(media).to_payload()
                elif (
                    len(media["source"]) > MAX_INPUT_MEDIA_SOURCE_CHARS
                    or any(char in media["source"] for char in "\r\n[]")
                    or len(media["name"]) > MAX_INPUT_MEDIA_NAME_CHARS
                    or len(content_type) > MAX_INPUT_MEDIA_MIME_CHARS
                ):
                    raise ValueError("invalid Resident input file reference")
            except PermissionError:
                if strict:
                    raise
                continue
            except (OSError, ValueError) as exc:
                if strict:
                    raise ValueError(f"invalid Resident input {media_type}") from exc
                continue
        else:
            if strict:
                raise ValueError("unsupported Resident input media source type")
            continue
        key = (media_type, media["source"])
        if key in seen:
            continue
        seen.add(key)
        normalized.append(media)
    return normalized


def resident_message_with_file_references(
    message: str, attachments: list[dict[str, str]],
) -> str:
    references = [
        f"[file: {item['source']}]"
        for item in attachments
        if item["media_type"] == "file"
        and f"[file: {item['source']}]" not in message
    ]
    return "\n\n".join([message, *references])


def provider_message_with_native_media(
    record: dict[str, Any],
    message: str,
    input_media: list[dict[str, str]],
) -> str:
    """Keep native attachments out of text; retain paths for tool-based readers."""

    text = str(message or "")
    if not input_media:
        return text
    definition = AGENT_PROVIDERS.get(resident_backend.execution_backend(record))
    if definition and definition.image_input_transport == "path_reference":
        staged_by_original = {
            str(item.get("original_source") or ""): str(item.get("source") or "")
            for item in input_media if item.get("original_source")
        }
        text = RESIDENT_IMAGE_MARKER_RE.sub(
            lambda match: f"[image: {staged_by_original.get(match.group(1).strip(), match.group(1).strip())}]",
            text,
        )
        markers = [
            f"[image: {str(item.get('source') or '')}]"
            for item in input_media
            if (
                str(item.get("media_type") or "") == "image"
                and str(item.get("source") or "")
                and str(item.get("source") or "") not in text
            )
        ]
        return "\n\n".join([text, *markers]).strip()
    names_by_source = {
        source: str(item.get("name") or "image")
        for item in input_media
        if str(item.get("source_type") or "") == "local_path"
        for source in (str(item.get("source") or ""), str(item.get("original_source") or ""))
        if source
    }

    def replace_marker(match: re.Match[str]) -> str:
        raw_source = match.group(1).strip()
        try:
            source = str(Path(raw_source).expanduser().resolve(strict=False))
        except (OSError, ValueError):
            return match.group(0)
        name = names_by_source.get(source)
        return f"[attached image: {name}]" if name else match.group(0)

    return RESIDENT_IMAGE_MARKER_RE.sub(replace_marker, text)
