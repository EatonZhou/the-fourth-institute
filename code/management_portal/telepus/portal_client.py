"""Portal access for the Telegram bridge (decision_66f5be947b67).

Every call is a signed HATG ISA instruction made as the Portal User a Telegram
contact is bound to (``entry=web``); the bridge never acts as Telepus or any
other identity and imports no Portal domain service.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any

from management_common.portal_paths import path as portal_path

RESIDENT_ID_RE = re.compile(r"^resident_[0-9a-f]{12}$")
ATTACHMENT_MAX_BYTES = int(os.getenv("TELEPUS_ATTACHMENT_MAX_BYTES", "20971520"))
ATTACHMENT_RESOURCE_RE = re.compile(
    r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*_[0-9a-f]{12}"
    r"(?:/(?:[A-Za-z0-9._~:@+\-]|%[0-9A-Fa-f]{2})+)*$"
)
MARKDOWN_LINK_RE = re.compile(r"\[(?P<label>[^\]\n]+)\]\((?P<target><[^>\n]+>|[^)\n]+)\)")
EXPLICIT_ATTACHMENT_LABEL_RE = re.compile(
    r"^\s*(?:附件|下载|attachment|download)\s*(?:[:：-]\s*)?(?P<name>.*)$",
    re.IGNORECASE,
)
AUTOMATIC_ATTACHMENT_EXTENSIONS = {
    ".7z", ".avi", ".bmp", ".bz2", ".csv", ".doc", ".docx", ".epub",
    ".flac", ".gif", ".gz", ".jpeg", ".jpg", ".m4a", ".mkv", ".mov",
    ".mp3", ".mp4", ".odp", ".ods", ".odt", ".ogg", ".pdf", ".png",
    ".ppt", ".pptx", ".rar", ".rtf", ".svg", ".tar", ".tgz", ".tif",
    ".tiff", ".wav", ".webm", ".webp", ".xls", ".xlsx", ".xz", ".zip",
}
AUTOMATIC_ATTACHMENT_MIME_PREFIXES = ("image/", "audio/", "video/")
AUTOMATIC_ATTACHMENT_MIME_TYPES = {
    "application/epub+zip", "application/gzip", "application/msword", "application/pdf",
    "application/rtf", "application/vnd.ms-excel", "application/vnd.ms-powerpoint",
    "application/vnd.oasis.opendocument.presentation", "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/x-7z-compressed", "application/x-bzip2", "application/x-rar-compressed",
    "application/x-tar", "application/zip",
}
PORTAL_CHAT_ATTACHMENT_ROOTS = tuple(
    portal_path('uploads') / name for name in ('resident_chat_attachments', 'resident_chat_images')
)
TERMINAL_INPUT_STATES = {"completed", "failed", "cancelled", "rejected", "lost_input", "expired"}


def _prepare_portal_runtime() -> None:
    os.environ.setdefault("PORTAL_ISA_ROUTING", "socket")
    os.environ.setdefault("PORTAL_SOCIETY_SERVICE_MODE", "client")
    os.environ.setdefault("PORTAL_RESOURCE_SERVICE_MODE", "client")


def message_text(message: dict[str, Any]) -> str:
    return "\n".join(
        str(part.get("text") or "")
        for part in message.get("content_parts") or []
        if isinstance(part, dict) and str(part.get("type") or "") == "text"
    ).strip()


def is_terminal(state: str) -> bool:
    return str(state or "") in TERMINAL_INPUT_STATES


class PortalClient:
    """HATG ISA calls as the contact's Portal User."""

    # -- transport ---------------------------------------------------------

    def call(self, actor: str, instruction: str, arguments: dict[str, Any], *, key: str = "") -> Any:
        _prepare_portal_runtime()
        from management_portal import isa_client

        return isa_client.call(actor, instruction, arguments, entry="web", key=key)

    def first_batch(self, actor: str, instruction: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        """The first batch of a streaming instruction; the subscription is closed after it."""
        _prepare_portal_runtime()
        from management_portal.hatg import routing
        from management_portal.hatg.contracts import InstructionContext

        stream = routing.stream({"instruction": instruction, "arguments": arguments},
                                InstructionContext(caller=actor, entry="web"))
        try:
            batch = next(iter(stream), [])
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()
        return [item for item in batch or [] if isinstance(item, dict)]

    # -- targets -------------------------------------------------------------

    def personal_assistant(self, actor: str) -> dict[str, str]:
        _prepare_portal_runtime()
        from management_portal.personal_ama import personal_assistant_target

        return personal_assistant_target(actor)

    def resident(self, actor: str, resident_id: str) -> dict[str, Any]:
        if not RESIDENT_ID_RE.fullmatch(str(resident_id or "")):
            raise ValueError("invalid resident id")
        return self.call(actor, "GetResident", {"resident_id": resident_id}).get("resident") or {}

    # -- messages --------------------------------------------------------------

    def deliver(
        self,
        actor: str,
        resident_id: str,
        message: dict[str, Any],
        *,
        activation_policy: str = "enqueue_and_trigger",
        temporary_credentials: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "resident_id": resident_id, "message": message, "activation_policy": activation_policy,
        }
        if temporary_credentials:
            arguments["temporary_credentials"] = temporary_credentials
        return self.call(actor, "DeliverMessage", arguments, key=f"telepus-deliver:{message['message_id']}")

    def conversation(self, actor: str, resident_id: str, *, history_limit: int = 0,
                     input_message_id: str = "") -> dict[str, Any]:
        arguments: dict[str, Any] = {"resident_id": resident_id}
        if history_limit:
            arguments["history_limit"] = int(history_limit)
        if input_message_id:
            arguments["input_message_id"] = input_message_id
        return self.call(actor, "GetConversation", arguments)

    def earlier_messages(self, actor: str, resident_id: str, cursor: str, limit: int = 200) -> dict[str, Any]:
        return self.call(actor, "ListMessages", {"resident_id": resident_id, "cursor": cursor, "limit": limit})

    def events(self, actor: str, resident_id: str, after_cursor: int, limit: int = 500) -> tuple[list[dict[str, Any]], int]:
        """Conversation events after ``after_cursor`` and the cursor to resume from."""
        items = self.first_batch(actor, "WatchConversationEvents", {
            "resident_id": resident_id, "after_cursor": int(after_cursor), "limit": int(limit),
        })
        events = [item for item in items if isinstance(item.get("event"), dict)]
        cursor = max([int(after_cursor), *(int(item.get("cursor") or 0) for item in items)])
        return events, cursor

    def input_state(self, actor: str, resident_id: str, message_id: str) -> dict[str, Any]:
        return self.call(actor, "GetInputState", {"resident_id": resident_id, "message_id": message_id}).get("input_state") or {}

    def cancel_input(self, actor: str, resident_id: str, message_id: str) -> dict[str, Any]:
        return self.call(actor, "CancelInput", {"resident_id": resident_id, "message_id": message_id})

    def resident_state(self, actor: str, resident_id: str) -> dict[str, Any]:
        states = self.call(actor, "ListResidentStates", {
            "resident_ids": [resident_id], "include_viewer_state": False,
        }).get("states") or []
        state = states[0] if states and isinstance(states[0], dict) else {}
        return state.get("state") if isinstance(state.get("state"), dict) else state

    def mailbox(self, actor: str, resident_id: str) -> list[dict[str, Any]]:
        pending = self.call(actor, "GetMailbox", {"resident_id": resident_id}).get("pending") or []
        return [item for item in pending if isinstance(item, dict) and item.get("kind") != "info"]

    def prioritize(self, actor: str, resident_id: str, message_id: str) -> dict[str, Any]:
        first = next((str(item.get("message_id") or "") for item in self.mailbox(actor, resident_id)), "")
        if not first or first == message_id:
            return {"reordered": False}
        return self.call(actor, "ReorderMailbox", {
            "resident_id": resident_id, "message_id": message_id, "before_message_id": first,
        })

    def inject(self, actor: str, resident_id: str, message_id: str) -> dict[str, Any]:
        return self.call(actor, "InjectMailboxItem", {"resident_id": resident_id, "message_id": message_id})

    def close_questions(self, actor: str, resident_id: str, source_message_id: str) -> dict[str, Any]:
        return self.call(actor, "AnswerResidentRequest", {
            "resident_id": resident_id, "message_id": source_message_id, "answers": {"action": "cancel"},
        })

    # -- attachments -------------------------------------------------------------

    def attachments(self, actor: str, resident_id: str, message: dict[str, Any]) -> list[dict[str, Any]]:
        """Downloadable files a reply carries or links; at most 12."""
        try:
            resident = self.resident(actor, resident_id)
        except Exception:  # noqa: BLE001 - a reply stays readable without its files
            resident = {}
        candidates: list[dict[str, Any] | None] = []
        for entry in message.get("content_parts") or []:
            if not isinstance(entry, dict) or str(entry.get("type") or "") not in {"image", "file"}:
                continue
            address = str(entry.get("resource_address") or entry.get("address") or "").strip().strip("/")
            if ATTACHMENT_RESOURCE_RE.fullmatch(address):
                candidates.append(_resource_candidate(address, entry))
                continue
            candidates.append(self._local_candidate(entry.get("path"), resident=resident, actor=actor,
                                                    resident_id=resident_id, filename=entry.get("filename"),
                                                    content_type=str(entry.get("content_type") or entry.get("mime_type") or "")))
        for match in MARKDOWN_LINK_RE.finditer(message_text(message)):
            label = str(match.group("label") or "").strip()
            explicit_name = _explicit_attachment_name(label)
            raw_target = str(match.group("target") or "").strip()
            target = raw_target[1:-1].strip() if raw_target.startswith("<") and raw_target.endswith(">") else raw_target.split(None, 1)[0]
            target = urllib.parse.unquote(target)
            address = target.strip().strip("/")
            if ATTACHMENT_RESOURCE_RE.fullmatch(address):
                candidate = _resource_candidate(address, {"title": explicit_name or label})
                if explicit_name is not None or _automatic(candidate):
                    candidates.append(candidate)
                continue
            parsed = urllib.parse.urlparse(target)
            if parsed.scheme or parsed.netloc or target.startswith("/api/"):
                continue
            candidate = self._local_candidate(target, resident=resident, actor=actor, resident_id=resident_id,
                                              filename=explicit_name or "", content_type="")
            if candidate and (explicit_name is not None or _automatic(candidate)):
                candidates.append(candidate)
        unique: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for candidate in candidates:
            if not candidate:
                continue
            identity = (str(candidate.get("source_kind")), str(candidate.get("resource_address") or candidate.get("path") or ""))
            if identity[1] and identity not in seen:
                seen.add(identity)
                unique.append(candidate)
        return unique[:12]

    def read_attachment(self, actor: str, attachment: dict[str, Any]) -> dict[str, Any]:
        source_kind = str(attachment.get("source_kind") or "")
        if source_kind == "resource":
            address = str(attachment.get("resource_address") or "").strip().strip("/")
            if not ATTACHMENT_RESOURCE_RE.fullmatch(address):
                raise ValueError("invalid resource attachment address")
            result = self.call(actor, "ReadResource", {"address": address, "max_bytes": ATTACHMENT_MAX_BYTES})
            if result.get("download_url") and not (result.get("base64") or result.get("text")):
                raise ValueError(f"附件超过 Telepus 大小限制：{ATTACHMENT_MAX_BYTES // (1024 * 1024)} MB")
            data = base64.b64decode(result["base64"]) if result.get("base64") else str(result.get("text") or "").encode("utf-8")
            return {"filename": _filename(attachment.get("filename"), result.get("name"), address.rsplit("/", 1)[-1]),
                    "content_type": str(result.get("content_type") or attachment.get("content_type") or "application/octet-stream"),
                    "data": data, "size": len(data), "source_kind": source_kind}
        if source_kind != "local_file":
            raise ValueError("unsupported attachment source")
        resident_id = str(attachment.get("resident_id") or "")
        path = self._validated_local_path(attachment.get("path"), resident=self.resident(actor, resident_id),
                                          actor=actor, resident_id=resident_id)
        data = path.read_bytes()
        if len(data) > ATTACHMENT_MAX_BYTES:
            raise ValueError(f"附件超过 Telepus 大小限制：{ATTACHMENT_MAX_BYTES // (1024 * 1024)} MB")
        return {"filename": _filename(attachment.get("filename"), path.name),
                "content_type": str(attachment.get("content_type") or mimetypes.guess_type(path.name)[0] or "application/octet-stream"),
                "data": data, "size": len(data), "source_kind": source_kind}

    def _local_candidate(self, raw_path: object, *, resident: dict[str, Any], actor: str, resident_id: str,
                         filename: object, content_type: str) -> dict[str, Any] | None:
        try:
            path = self._validated_local_path(raw_path, resident=resident, actor=actor, resident_id=resident_id)
        except (OSError, PermissionError, ValueError):
            return None
        size = path.stat().st_size
        if size > ATTACHMENT_MAX_BYTES:
            return None
        return {"source_kind": "local_file", "resident_id": resident_id, "path": str(path),
                "filename": _filename(filename, path.name),
                "content_type": str(content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"),
                "size": size}

    @staticmethod
    def _validated_local_path(raw_path: object, *, resident: dict[str, Any], actor: str, resident_id: str) -> Path:
        text = str(raw_path or "").strip()
        if not text:
            raise ValueError("attachment path missing")
        path = Path(text).expanduser()
        if not path.is_absolute():
            default_cwd = str(resident.get("default_cwd") or "").strip()
            if not default_cwd:
                raise PermissionError("relative attachment path has no registered cwd")
            path = Path(default_cwd) / path
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            raise FileNotFoundError(str(resolved))
        roots = [Path(str(entry.get("path"))).expanduser().resolve(strict=False)
                 for entry in resident.get("workspaces") or [] if isinstance(entry, dict) and entry.get("path")]
        if resident.get("default_cwd"):
            roots.append(Path(str(resident["default_cwd"])).expanduser().resolve(strict=False))
        if any(resolved.is_relative_to(root) for root in roots):
            return resolved
        for root in PORTAL_CHAT_ATTACHMENT_ROOTS:
            if resolved.is_relative_to(root.resolve(strict=False)):
                try:
                    metadata = json.loads(resolved.with_suffix(resolved.suffix + ".json").read_text(encoding="utf-8"))
                except Exception as exc:
                    raise PermissionError("Portal attachment metadata missing") from exc
                if str(metadata.get("actor") or "").strip().casefold() != str(actor or "").strip().casefold():
                    raise PermissionError("Portal attachment actor mismatch")
                if metadata.get("resident_id") and str(metadata["resident_id"]) != resident_id:
                    raise PermissionError("Portal attachment Resident mismatch")
                return resolved
        raise PermissionError("attachment is outside registered Resident workspaces")


def _explicit_attachment_name(label: str) -> str | None:
    match = EXPLICIT_ATTACHMENT_LABEL_RE.fullmatch(str(label or ""))
    return str(match.group("name") or "").strip() if match else None


def _automatic(candidate: dict[str, Any]) -> bool:
    content_type = str(candidate.get("content_type") or "").split(";", 1)[0].strip().lower()
    if content_type.startswith(AUTOMATIC_ATTACHMENT_MIME_PREFIXES) or content_type in AUTOMATIC_ATTACHMENT_MIME_TYPES:
        return True
    names = (str(candidate.get("filename") or ""), str(candidate.get("resource_address") or "").rsplit("/", 1)[-1],
             str(candidate.get("path") or ""))
    return any(Path(name).suffix.lower() in AUTOMATIC_ATTACHMENT_EXTENSIONS for name in names if name)


def _resource_candidate(address: str, reference: dict[str, Any]) -> dict[str, Any]:
    return {"source_kind": "resource", "resource_address": address,
            "filename": _filename(reference.get("filename"), reference.get("title"), address.rsplit("/", 1)[-1]),
            "content_type": str(reference.get("content_type") or reference.get("mime_type")
                                or mimetypes.guess_type(address)[0] or "application/octet-stream"),
            "size": max(0, int(reference.get("size") or 0))}


def _filename(*values: object) -> str:
    for value in values:
        text = str(value or "").strip().replace("\r", "").replace("\n", "")
        name = Path(text).name.strip().replace('"', "") if text else ""
        if name:
            return name[:180]
    return "attachment"
